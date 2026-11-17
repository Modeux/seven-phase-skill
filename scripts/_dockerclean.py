# -*- coding: utf-8 -*-
"""Find and remove leftover testcontainers; shared by gate.py, dockerslot.py and cleanup.py (stdlib only).

A container labelled org.testcontainers=true is a leftover when
  exited      its state is dead or exited;
  owner-gone  no live process owns its session (testcontainers-go on macOS only, see VERIFIED_GO);
  old         its owner cannot be judged and it is older than the longest gate timeout.
A container whose owner is alive is kept whatever its age. Unlabelled containers (the dev stack, compose services)
are never listed.
"""
import calendar
import datetime
import hashlib
import os
import re
import subprocess
import sys
import time

TC_LABEL = "org.testcontainers"
# WHY: older than the longest gate timeout (2400 s), so such a container cannot belong to a live gate run.
ORPHAN_AGE_S = 50 * 60
# WHY: testcontainers-go labels every container with sha256("testcontainers-go:<pid>:<start ms>") of the process
# that started the test binary (`go test`), so a session no live process hashes to belongs to a run that has ended.
SESSION_SEED = b"testcontainers-go:%d:%d"
SESSION_RE = re.compile(r"^[0-9a-f]{64}$")
# INVARIANT: list a version only after checking it against a live run: during an integration test,
# `gate.py clean-docker` must count that run's containers under kept=. A scheme that changed would match no
# process, and every running container of that version would look ownerless and be removed mid-test.
VERIFIED_GO = ("0.41.0",)
# WHY: Ryuk or a parallel sweep removing the same container first is the outcome we wanted, not a failure.
GONE_RE = re.compile(r"No such container|is already in progress")
DOCKER_TIMEOUT_S = 60
_FIELDS = ["{{.ID}}", "{{.State}}", "{{.CreatedAt}}", '{{.Label "%s"}}' % TC_LABEL, "{{.Names}}",
           '{{.Label "%s.sessionId"}}' % TC_LABEL, '{{.Label "%s.lang"}}' % TC_LABEL,
           '{{.Label "%s.version"}}' % TC_LABEL]


class CleanError(Exception):
    """Docker or ps could not be read; nothing was judged from a partial listing."""


def _short(text):
    lines = [x for x in text.strip().splitlines() if x.strip()]
    return lines[0][:300] if lines else "(no output)"


def rows():
    """Every labelled container: id, state, age_s, name, session, lang, version."""
    try:
        r = subprocess.run(["docker", "ps", "-a", "--filter", f"label={TC_LABEL}=true", "--format", "\t".join(_FIELDS)],
                           capture_output=True, text=True, timeout=DOCKER_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise CleanError(f"docker ps failed: {e}")
    if r.returncode != 0:
        raise CleanError(f"docker ps failed: {_short(r.stderr or r.stdout)}")
    out, now = [], time.time()
    for line in r.stdout.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) != len(_FIELDS):
            raise CleanError(f"unexpected docker ps row: {_short(line)}")
        cid, state, created, label, name, session, lang, version = cols
        try:
            when = datetime.datetime.strptime(" ".join(created.split(" ")[:3]), "%Y-%m-%d %H:%M:%S %z").timestamp()
        except ValueError:
            raise CleanError(f"unparsable CreatedAt in docker ps row: {_short(line)}")
        # INVARIANT: re-check the label; removing an unlabelled container would hit the dev stack or another
        # session's services.
        if label == "true":
            out.append({"id": cid, "state": state, "age_s": now - when, "name": name, "session": session,
                        "lang": lang, "version": version})
    return out


def live_sessions(wanted):
    """The members of `wanted` (session ids) that a live process still owns."""
    if not wanted:
        return set()
    try:
        r = subprocess.run(["ps", "-axo", "pid=,lstart="], capture_output=True, text=True, timeout=30,
                           env=dict(os.environ, TZ="UTC", LC_ALL="C"))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise CleanError(f"ps failed: {e}")
    if r.returncode != 0:
        raise CleanError(f"ps failed: {_short(r.stderr or r.stdout)}")
    live, pids = set(), set()
    for line in r.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        try:
            pid = int(parts[0])
            base = calendar.timegm(time.strptime(" ".join(parts[1:6]), "%a %b %d %H:%M:%S %Y")) * 1000
        except ValueError:
            raise CleanError(f"unparsable ps row: {_short(line)}")
        pids.add(pid)
        # WHY: ps prints whole seconds and the session id hashes milliseconds; 1000 hashes per process is ~0.2 s
        # for the whole table.
        for ms in range(1000):
            session = hashlib.sha256(SESSION_SEED % (pid, base + ms)).hexdigest()
            if session in wanted:
                live.add(session)
    # INVARIANT: a truncated process list would make live owners look gone.
    if 1 not in pids:
        raise CleanError("ps listing has no pid 1; refusing to judge owners from it")
    return live


def _checkable(row):
    return (sys.platform == "darwin" and row["lang"] == "go" and row["version"] in VERIFIED_GO
            and bool(SESSION_RE.match(row["session"])))


def split(listed):
    """(leftovers, notes) for rows() output; each leftover gains "why": exited | owner-gone | old."""
    # INVARIANT: `listed` was read before the process table is. An owner always starts before its containers, so
    # a container in the listing whose owner is missing from a later process table lost it; in the other order a
    # run that started in between would have containers and no visible owner.
    open_rows = [r for r in listed if r["state"] not in ("dead", "exited")]
    notes, live, judged = [], set(), False
    wanted = {r["session"] for r in open_rows if _checkable(r)}
    if wanted:
        try:
            live, judged = live_sessions(wanted), True
        except CleanError as e:
            notes.append(f"owner check skipped ({e}); running containers judged by age only")
    unverified = sorted({r["version"] for r in open_rows if r["lang"] == "go" and r["version"] not in VERIFIED_GO})
    if unverified:
        notes.append(f"testcontainers-go {', '.join(unverified)} is not in VERIFIED_GO; "
                     "its running containers are judged by age only")
    found = []
    for r in listed:
        if r["state"] in ("dead", "exited"):
            why = "exited"
        elif judged and _checkable(r):
            why = None if r["session"] in live else "owner-gone"
        else:
            why = "old" if r["age_s"] > ORPHAN_AGE_S else None
        if why:
            found.append(dict(r, why=why))
    return found, notes


def remove(ids):
    if not ids:
        return
    try:
        # WHY -v: a postgres testcontainer owns an anonymous data volume that outlives a plain `rm -f`.
        r = subprocess.run(["docker", "rm", "-f", "-v"] + list(ids), capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise CleanError(f"docker rm failed: {e}")
    if r.returncode != 0:
        bad = [x for x in (r.stderr or r.stdout).splitlines() if x.strip() and not GONE_RE.search(x)]
        if bad:
            raise CleanError(f"docker rm failed: {_short(bad[0])}")


def sweep():
    """Remove every leftover; returns (removed rows, notes)."""
    found, notes = split(rows())
    remove([r["id"] for r in found])
    return found, notes


def sweep_quietly():
    """(removed count, error text or None); for callers whose own result must not depend on the cleanup."""
    try:
        found, _ = sweep()
        return len(found), None
    except CleanError as e:
        return 0, str(e)

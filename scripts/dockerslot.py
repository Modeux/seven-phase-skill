# -*- coding: utf-8 -*-
"""Run a command only while holding one of N machine-wide slots, so parallel agents share Docker
without being dispatched in waves.

Usage:
  python3 dockerslot.py -- env -u GOROOT go test -C <tree>/backend-golang -tags integration -run X ./internal/foo/
  python3 dockerslot.py --all --max-wait 1800 -- <full integration gate>   # takes every slot; newcomers queue behind it
  python3 dockerslot.py --pool sqlc --slots 1 -- just sqlc
  python3 dockerslot.py --status                                            # who holds what

Exit code is the command's. 75 = gave up waiting (--max-wait); stderr names the holders.
Slots are flock'd files under $DOCKERSLOT_DIR (default ~/.cache/seven-phase/dockerslot); a crashed holder
releases its slot when its process dies. Slot count: --slots, else $DOCKERSLOT_SLOTS, else 3.
"""
import argparse
import fcntl
import json
import os
import pathlib
import signal
import subprocess
import sys
import time

# WHY: 3 matches the widest wave that ran integration tests without testcontainers "port not found"
# flakes or address-pool exhaustion (age-gate-unify verifiers, 10-14). Raise it only after a probe.
DEFAULT_SLOTS = 3
EX_TEMPFAIL = 75
POLL_S = 1.0


def slot_dir():
    return pathlib.Path(os.environ.get("DOCKERSLOT_DIR") or pathlib.Path.home() / ".cache/seven-phase/dockerslot")


def open_lock(path):
    return os.open(path, os.O_RDWR | os.O_CREAT, 0o644)


def try_lock(fd, mode):
    try:
        fcntl.flock(fd, mode | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def holders(d, pool, slots):
    out = []
    for i in range(slots):
        p = d / f"{pool}-{i}.lock"
        fd = open_lock(p)
        try:
            if try_lock(fd, fcntl.LOCK_EX):
                fcntl.flock(fd, fcntl.LOCK_UN)
                continue
            info = p.read_text(encoding="utf-8").strip()
            out.append(f"slot {i + 1}: {info or '(held, no info)'}")
        finally:
            os.close(fd)
    return out


def write_holder(fd, cmd):
    info = json.dumps({"pid": os.getpid(), "since": time.strftime("%H:%M:%S"), "cwd": os.getcwd(),
                       "cmd": " ".join(cmd)[:200]}, ensure_ascii=False)
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, info.encode("utf-8"))


def acquire(d, pool, slots, take_all, max_wait, cmd):
    """Return the held slot fds, or None after max_wait seconds."""
    deadline = time.monotonic() + max_wait
    gate = open_lock(d / f"{pool}-all.lock")
    fds = [open_lock(d / f"{pool}-{i}.lock") for i in range(slots)]
    announced = False

    def wait_once():
        nonlocal announced
        if not announced:
            busy = holders(d, pool, slots)
            print(f"dockerslot: waiting for {'all ' if take_all else ''}{pool} slot(s); "
                  f"{len(busy)}/{slots} busy" + "".join(f"\n  {h}" for h in busy), file=sys.stderr)
            announced = True
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_S)
        return True

    if take_all:
        # INVARIANT: the gate is taken exclusively before the slots, and single-slot callers need it
        # shared to start — so a waiting --all blocks newcomers instead of starving behind them.
        while not try_lock(gate, fcntl.LOCK_EX):
            if not wait_once():
                return None
        for fd in fds:
            while not try_lock(fd, fcntl.LOCK_EX):
                if not wait_once():
                    return None
            write_holder(fd, cmd)
        return fds
    while True:
        if try_lock(gate, fcntl.LOCK_SH):
            for fd in fds:
                if try_lock(fd, fcntl.LOCK_EX):
                    fcntl.flock(gate, fcntl.LOCK_UN)
                    write_holder(fd, cmd)
                    return [fd]
            fcntl.flock(gate, fcntl.LOCK_UN)
        if not wait_once():
            return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pool", default="docker", help="independent slot pool name (default: docker)")
    ap.add_argument("--slots", type=int, default=int(os.environ.get("DOCKERSLOT_SLOTS") or DEFAULT_SLOTS))
    ap.add_argument("--all", action="store_true", help="hold every slot of the pool (full integration gate)")
    ap.add_argument("--max-wait", type=float, default=300, help="seconds to wait before exit 75 (default 300)")
    ap.add_argument("--status", action="store_true", help="print current holders and exit")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd
    if a.slots < 1:
        ap.error("--slots must be >= 1")
    d = slot_dir()
    d.mkdir(parents=True, exist_ok=True)
    if a.status:
        busy = holders(d, a.pool, a.slots)
        print(f"{a.pool}: {len(busy)}/{a.slots} busy" + "".join(f"\n  {h}" for h in busy))
        return 0
    if not cmd:
        ap.error("missing command after --")

    t0 = time.monotonic()
    fds = acquire(d, a.pool, a.slots, a.all, a.max_wait, cmd)
    if fds is None:
        busy = holders(d, a.pool, a.slots)
        print(f"dockerslot: gave up after {a.max_wait:.0f}s; {len(busy)}/{a.slots} {a.pool} slots busy"
              + "".join(f"\n  {h}" for h in busy) + "\nretry, or ask the parent session", file=sys.stderr)
        return EX_TEMPFAIL
    waited = time.monotonic() - t0
    if waited >= POLL_S:
        print(f"dockerslot: got {'all ' if a.all else ''}{a.pool} slot(s) after {waited:.0f}s", file=sys.stderr)

    child = subprocess.Popen(cmd)

    def forward(sig, _frame):
        child.send_signal(sig)

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, forward)
    rc = child.wait()
    return 128 - rc if rc < 0 else rc


if __name__ == "__main__":
    sys.exit(main())

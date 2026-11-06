#!/bin/bash
# Brings up a deepweave walk environment for one tree: database, gateway, frontend. Does things, judges nothing.
# A kit's env.sh sets the values and calls this, then adds its own seed data (assets/walk-kit.md):
#
#   export TREE=<tree> WALK=<evidence dir> DB=deepweave_walk_<slug> GW_PORT=7512 FE_PORT=4310   # ADMIN_PORT=4311 if the walk needs admin
#   ~/.claude/skills/seven-phase/scripts/walk_env.sh "$@"
#
#   walk_env.sh <round>          bring everything up (idempotent) and print what serves what
#   walk_env.sh <round> stop     stop the gateway and frontends; the database stays
#   walk_env.sh <round> dropdb   stop, then drop the walk database
#
# Exit 2 when a port is held by another tree, because measurements there would not be this change.
#
# WHY its own database by default: River's job table lives in the database, so on the shared dev DB the dev gateway
# and other worktrees' gateways take this tree's jobs; it also keeps an uncommitted migration off the shared one.
# DB=deepweave uses the shared one and skips create, migrate and seed: only for walks that start no jobs.
set -u
ROUND=${1:?usage: walk_env.sh <round> [stop|dropdb]}
ACTION=${2:-up}
: "${TREE:?set TREE}" "${WALK:?set WALK}" "${DB:?set DB}" "${GW_PORT:?set GW_PORT}" "${FE_PORT:?set FE_PORT}"
ADMIN_PORT=${ADMIN_PORT:-}
MAIN=/Users/huntwu/Desktop/deepweave/deepweave
PG=deepweave-postgres
# WHY: two walks at once must not share caches. Pub/Sub is not scoped by index: a WS event for a seed user id
# published by another gateway can still reach this one, so data checks go through SQL or curl.
REDIS_INDEX=${REDIS_INDEX:-$(( GW_PORT % 12 + 3 ))}
TREE=$(cd "$TREE" && pwd)
RUN=$WALK/$ROUND/run
mkdir -p "$RUN" "$WALK/bin" "$WALK/pids"
say() { echo "$1" | tee -a "$RUN/env.log"; }
listening() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -1; }
ports() { echo "$FE_PORT $GW_PORT $ADMIN_PORT"; }
stop_one() {
  local f=$WALK/pids/$1.pid pid
  [ -f "$f" ] || return 0
  pid=$(cat "$f")
  if kill -0 "$pid" 2>/dev/null; then kill "$pid" 2>/dev/null; sleep 1; kill -9 "$pid" 2>/dev/null; fi
  rm -f "$f"
  say "stopped $1 (pid $pid)"
}
served_from_this_tree() {
  local pid cwd
  pid=$(listening "$1")
  cwd=$(lsof -a -d cwd -p "$pid" -Fn 2>/dev/null | sed -n 's/^n//p')
  case "$cwd" in "$TREE"|"$TREE"/*) return 0;; esac
  say "port $1 is held by pid $pid serving $cwd, not $TREE"
  return 1
}

if [ "$ACTION" = "stop" ] || [ "$ACTION" = "dropdb" ]; then
  stop_one gateway; stop_one frontend; stop_one admin
  for p in $(ports); do
    pid=$(listening "$p")
    [ -n "$pid" ] && served_from_this_tree "$p" && { kill "$pid" 2>/dev/null; say "killed listener on :$p (pid $pid)"; }
  done
  if [ "$ACTION" = "dropdb" ] && [ "$DB" != "deepweave" ]; then
    docker exec "$PG" dropdb -U "${POSTGRES_USER:-deepweave}" --if-exists "$DB" && say "dropped database $DB"
  fi
  exit 0
fi

: > "$RUN/env.log"
say "tree: $TREE  (HEAD $(git -C "$TREE" rev-parse --short HEAD), $(git -C "$TREE" status --porcelain | wc -l | tr -d ' ') uncommitted paths)"
for p in $(ports); do
  [ -z "$(listening "$p")" ] || served_from_this_tree "$p" || exit 2
done

# A worktree has no .env of its own. Never echoed.
set -a
# shellcheck disable=SC1091
. "$MAIN/.env"
set +a
PGUSER_=${POSTGRES_USER:-deepweave}
base="${DATABASE_URL/@postgres:/@localhost:}"
query=""
case "$base" in *\?*) query="?${base#*\?}"; base="${base%%\?*}";; esac
WALK_DB_URL="${base%/*}/$DB$query"
redis="${REDIS_URL/\/\/redis:/\/\/localhost:}"
redis="${redis/@redis:/@localhost:}"
redis=$(echo "$redis" | sed -E 's#/[0-9]+$##')
WALK_REDIS_URL="$redis/$REDIS_INDEX"
WALK_AI_URL="${AI_SERVICE_URL/\/\/ai-service:/\/\/localhost:}"

if [ "$DB" != "deepweave" ]; then
  exists=$(docker exec "$PG" psql -U "$PGUSER_" -d postgres -Atc "select 1 from pg_database where datname='$DB'")
  if [ "$exists" != "1" ]; then
    docker exec "$PG" createdb -U "$PGUSER_" "$DB" || { say "createdb failed"; exit 2; }
    say "created database $DB"
  fi
  # WHY: public.mem0 belongs to the AI service (Mem0 creates it), yet migrations index it; the same stub the
  # integration harness uses (internal/testutil/testutil.go) lets a from-zero migrate pass.
  docker exec "$PG" psql -q -v ON_ERROR_STOP=1 -U "$PGUSER_" -d "$DB" -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE TABLE IF NOT EXISTS public.mem0 (id UUID PRIMARY KEY, vector vector(1536), payload JSONB);" > "$RUN/mem0-stub.out" 2>&1
  ( cd "$TREE" && MIGRATE_DATABASE_URL="$WALK_DB_URL" just --justfile "$TREE/justfile" --working-directory "$TREE" migrate-up ) > "$RUN/migrate.out" 2>&1
  status=$?
  say "migrate-up exit=$status (log: $RUN/migrate.out)"
  [ "$status" = "0" ] || exit 2
  seeded=$(docker exec "$PG" psql -U "$PGUSER_" -d "$DB" -Atc "select count(*) from users where email='creator.demo@deepweave.local'" 2>/dev/null)
  if [ "${seeded:-0}" = "0" ]; then
    tmp=/tmp/dw-walk-seeds-$DB
    docker exec "$PG" rm -rf "$tmp"
    docker cp "$TREE/db/seeds/." "$PG:$tmp" > /dev/null
    docker exec "$PG" psql -v ON_ERROR_STOP=1 -U "$PGUSER_" -d "$DB" -f "$tmp/dev_seed.sql" > "$RUN/seed.out" 2>&1
    status=$?
    say "dev seed exit=$status (log: $RUN/seed.out)"
    docker exec "$PG" rm -rf "$tmp"
    [ "$status" = "0" ] || exit 2
  fi
  say "goose version: $(docker exec "$PG" psql -U "$PGUSER_" -d "$DB" -Atc 'select max(version_id) from goose_db_version')"
fi

if [ -z "$(listening "$GW_PORT")" ]; then
  ( cd "$TREE/backend-golang" && env -u GOROOT go build -o "$WALK/bin/gateway" ./cmd/server/ ) > "$RUN/gateway-build.out" 2>&1
  status=$?
  say "gateway build exit=$status (log: $RUN/gateway-build.out)"
  [ "$status" = "0" ] || exit 2
  (
    cd "$TREE" || exit 2
    export GATEWAY_PORT=$GW_PORT DATABASE_URL="$WALK_DB_URL" REDIS_URL="$WALK_REDIS_URL" AI_SERVICE_URL="$WALK_AI_URL"
    export CORS_ORIGINS="http://localhost:$FE_PORT${ADMIN_PORT:+,http://localhost:$ADMIN_PORT}"
    export LOG_FILE="$RUN/gateway-app.log"
    # WHY: a second gateway must not send alerts or spend the shared Langfuse quota.
    export LANGFUSE_TRACING_ENABLED=false ENABLE_PPROF=false
    unset TELEGRAM_BOT_TOKEN TELEGRAM_ALERT_CHAT_ID LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY
    nohup "$WALK/bin/gateway" > "$RUN/gateway.log" 2>&1 &
    echo $! > "$WALK/pids/gateway.pid"
  )
  for _ in $(seq 1 60); do [ -n "$(listening "$GW_PORT")" ] && break; sleep 1; done
fi

cd "$TREE/angular-workspace" || exit 2
if [ ! -d node_modules ]; then
  pnpm install --frozen-lockfile > "$RUN/pnpm-install.out" 2>&1
  say "pnpm install exit=$? (log: $RUN/pnpm-install.out)"
fi
PUBLIC_API_BASE_URL="http://localhost:$GW_PORT" APP_ENV=development node scripts/generate-runtime-config.mjs > "$RUN/runtime-config.out" 2>&1
say "runtime config exit=$? -> $(grep -o 'apiBaseUrl[^,}]*' projects/frontend/public/runtime-config.js | head -1)"
unset PORT
if [ -z "$(listening "$FE_PORT")" ]; then
  nohup npx ng serve frontend --port "$FE_PORT" > "$RUN/ng-frontend.log" 2>&1 &
  echo $! > "$WALK/pids/frontend.pid"
fi
if [ -n "$ADMIN_PORT" ] && [ -z "$(listening "$ADMIN_PORT")" ]; then
  nohup npx ng serve admin --port "$ADMIN_PORT" > "$RUN/ng-admin.log" 2>&1 &
  echo $! > "$WALK/pids/admin.pid"
fi
for _ in $(seq 1 180); do
  [ -n "$(listening "$FE_PORT")" ] && { [ -z "$ADMIN_PORT" ] || [ -n "$(listening "$ADMIN_PORT")" ]; } && break
  sleep 1
done

if [ -d "$WALK/kit" ]; then
  printf '{"app":"http://localhost:%s","api":"http://localhost:%s","db":"%s"}\n' "$FE_PORT" "$GW_PORT" "$DB" > "$WALK/kit/walk.config.json"
fi
say "frontend http://localhost:$FE_PORT tree=$TREE pid=$(listening "$FE_PORT") (log: $RUN/ng-frontend.log)"
[ -z "$ADMIN_PORT" ] || say "admin    http://localhost:$ADMIN_PORT pid=$(listening "$ADMIN_PORT") (log: $RUN/ng-admin.log)"
say "gateway  http://localhost:$GW_PORT tree=$TREE health=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$GW_PORT/health") (log: $RUN/gateway.log)"
say "database $DB in container $PG; redis index $REDIS_INDEX"
say "started  $(date '+%Y-%m-%d %H:%M:%S')"

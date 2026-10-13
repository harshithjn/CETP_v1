#!/usr/bin/env bash
# Stand up a PostgreSQL version matrix on this machine (TASKS.md Tier 2, item 36).
#
# Why: every measurement in this project comes from one PostgreSQL version (15.17). Plan
# JSON format, planner cost model and parallelism defaults all move between major versions,
# so "the plan for query q" is version-dependent in the same way Tier 3 showed it is
# hardware-dependent. This is the cheapest test of whether the method survives that.
#
# Each version is loaded FROM SOURCE (schema-only DDL + the original CSVs) rather than by
# pg_restore, for two reasons:
#   - pg_dump requires version >= server, and a PG15 data dump cannot be restored into
#     PG14 at all (downgrades are unsupported).
#   - Loading from source guarantees byte-identical data on every version, so the only
#     thing that differs is the engine. That is the point of the experiment.
#
# macOS ships bash 3.2 (no associative arrays), so ports come from a function.
set -euo pipefail

# PostgreSQL 17+ on macOS aborts at startup with "postmaster became multithreaded during
# startup" when the locale is unset -- macOS's locale initialisation spawns a thread, and
# PG17 added a check that refuses to continue. Setting a valid locale avoids it. PG14/15
# are unaffected but setting it uniformly keeps every version's collation identical, which
# matters because collation affects sort order and therefore plan costs.
export LC_ALL=C
export LANG=C

PROJECT="/Users/harshithj/Main/Resources/CETP"
CSV_DIR="$PROJECT/scripts/collection/tpch_data"
SCHEMA_SQL="/tmp/cetp_tpch_schema.sql"
SOURCE_PREFIX="/opt/homebrew/opt/postgresql@15"
SOURCE_PORT=5432

port_for() {
  case "$1" in
    14) echo 5434 ;; 17) echo 5437 ;; 18) echo 5438 ;; *) echo "" ;;
  esac
}

TABLES="region nation customer supplier part partsupp orders lineitem"

if [ ! -f "$SCHEMA_SQL" ]; then
  echo "=== extracting schema-only DDL from the live PostgreSQL 15 cluster ==="
  # Plain-text DDL is portable across major versions; a binary data dump is not.
  "$SOURCE_PREFIX/bin/pg_dump" -p "$SOURCE_PORT" -d tpch --schema-only --no-owner \
    --no-privileges -f "$SCHEMA_SQL"
fi
echo "schema DDL: $(grep -ci 'create table' "$SCHEMA_SQL") tables, \
$(grep -ci 'create index\|create unique index' "$SCHEMA_SQL") indexes"

for v in 14 17 18; do
  port="$(port_for "$v")"
  prefix="/opt/homebrew/opt/postgresql@${v}"
  datadir="/opt/homebrew/var/postgresql@${v}"
  [ -d "$prefix" ] || { echo "postgresql@$v not installed, skipping"; continue; }

  echo "=== postgresql@$v on port $port ==="
  if [ ! -f "$datadir/PG_VERSION" ]; then
    echo "  initdb"; "$prefix/bin/initdb" -D "$datadir" >/dev/null
  fi
  if ! "$prefix/bin/pg_isready" -p "$port" -q 2>/dev/null; then
    echo "  starting"
    "$prefix/bin/pg_ctl" -D "$datadir" -o "-p $port" -l "/tmp/pg${v}.log" start >/dev/null 2>&1 || true
    for _ in $(seq 1 40); do
      "$prefix/bin/pg_isready" -p "$port" -q 2>/dev/null && break
      sleep 1
    done
  fi
  "$prefix/bin/pg_isready" -p "$port" -q || { echo "  FAILED to start; see /tmp/pg${v}.log"; continue; }
  echo "  server_version: $("$prefix/bin/psql" -p "$port" -d postgres -tAc 'show server_version')"

  "$prefix/bin/dropdb" -p "$port" --if-exists tpch
  "$prefix/bin/createdb" -p "$port" tpch

  # Schema without indexes first: building indexes after the load is far faster.
  grep -viE '^create (unique )?index' "$SCHEMA_SQL" > /tmp/cetp_schema_noidx.sql
  "$prefix/bin/psql" -p "$port" -d tpch -q -v ON_ERROR_STOP=1 -f /tmp/cetp_schema_noidx.sql

  echo "  loading CSVs"
  for t in $TABLES; do
    f="$CSV_DIR/${t}.csv"
    [ -f "$f" ] || { echo "    MISSING $f"; continue; }
    "$prefix/bin/psql" -p "$port" -d tpch -q -v ON_ERROR_STOP=1 \
      -c "\\copy $t from '$f' with (format csv, delimiter '|', quote '\"')"
  done

  echo "  building indexes"
  grep -iE '^create (unique )?index' "$SCHEMA_SQL" > /tmp/cetp_schema_idx.sql || true
  if [ -s /tmp/cetp_schema_idx.sql ]; then
    "$prefix/bin/psql" -p "$port" -d tpch -q -f /tmp/cetp_schema_idx.sql 2>&1 | head -3
  fi

  "$prefix/bin/psql" -p "$port" -d tpch -q -c "analyze"
  echo "  lineitem rows: $("$prefix/bin/psql" -p "$port" -d tpch -tAc 'select count(*) from lineitem')"
  echo "  indexes: $("$prefix/bin/psql" -p "$port" -d tpch -tAc "select count(*) from pg_indexes where schemaname='public'")"
  echo "  size: $("$prefix/bin/psql" -p "$port" -d tpch -tAc "select pg_size_pretty(pg_database_size('tpch'))")"
done

echo
echo "=== matrix ready ==="
echo "15 -> 5432 (existing)"
for v in 14 17 18; do echo "$v -> $(port_for "$v")"; done

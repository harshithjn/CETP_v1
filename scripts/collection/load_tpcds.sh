#!/usr/bin/env bash
# Load TPC-DS SF1 into a local PostgreSQL database (TASKS.md Tier 2, item 33).
#
# dsdgen emits pipe-delimited .dat files with no trailing delimiter, so COPY reads them
# directly. tpcds_ri.sql adds the primary/foreign keys; without them the planner has no
# uniqueness information and picks visibly worse plans than a real deployment would.
set -euo pipefail

DB="${1:-tpcds}"
DATA_DIR="${2:-/Users/harshithj/Main/Resources/CETP/data/external/tpcds_data}"
KIT="${3:-/Users/harshithj/Main/Resources/CETP/data/external/tpcds-kit/tools}"

echo "=== creating database $DB ==="
dropdb --if-exists "$DB"
createdb "$DB"

echo "=== schema (25 tables) ==="
psql -q -d "$DB" -v ON_ERROR_STOP=1 -f "$KIT/tpcds.sql"

echo "=== loading data ==="
for f in "$DATA_DIR"/*.dat; do
  t=$(basename "$f" .dat)
  n=$(psql -tAq -d "$DB" -c "select count(*) from information_schema.tables
                             where table_schema='public' and table_name='$t'")
  if [ "$n" != "1" ]; then echo "  skip $t (no such table)"; continue; fi
  printf "  %-24s " "$t"
  psql -q -d "$DB" -v ON_ERROR_STOP=1 \
    -c "\\copy $t from '$f' with (format csv, delimiter '|', null '')" 2>&1 | head -2
  psql -tAq -d "$DB" -c "select to_char(count(*),'FM999,999,999') from $t"
done

echo "=== keys and constraints ==="
psql -q -d "$DB" -f "$KIT/tpcds_ri.sql" 2>&1 | grep -ciE "error" || true

echo "=== analyze ==="
psql -q -d "$DB" -c "analyze"

echo "=== size ==="
psql -tAq -d "$DB" -c "select pg_size_pretty(pg_database_size('$DB'))"

#!/usr/bin/env bash
# Load the IMDB dataset for the Join Order Benchmark (TASKS.md Tier 2, item 33).
#
# JOB is the standard workload in the learned-query-optimization literature (Leis et al.,
# "How Good Are Query Optimizers, Really?", PVLDB 2015) and it is the only NON-SYNTHETIC
# workload in this project: real IMDB data with real skew and correlation, which is exactly
# what TPC-H's uniform generated data lacks.
#
# The CSVs use backslash escapes, so COPY needs `escape '\'` -- without it the load fails
# partway through on embedded quotes in film titles.
set -euo pipefail

DB="${1:-imdbload}"
DATA_DIR="${2:-/Users/harshithj/Main/Resources/CETP/data/external/imdb}"
JOB="${3:-/Users/harshithj/Main/Resources/CETP/data/external/job-queries}"

echo "=== creating database $DB ==="
dropdb --if-exists "$DB"
createdb "$DB"

echo "=== schema (21 tables) ==="
psql -q -d "$DB" -v ON_ERROR_STOP=1 -f "$JOB/schema.sql"

echo "=== loading 3.7 GB of CSV ==="
for f in "$DATA_DIR"/*.csv; do
  t=$(basename "$f" .csv)
  n=$(psql -tAq -d "$DB" -c "select count(*) from information_schema.tables
                             where table_schema='public' and table_name='$t'")
  if [ "$n" != "1" ]; then echo "  skip $t (no such table)"; continue; fi
  printf "  %-22s " "$t"
  psql -q -d "$DB" -v ON_ERROR_STOP=1 \
    -c "\\copy $t from '$f' with (format csv, escape '\')" 2>&1 | head -2
  psql -tAq -d "$DB" -c "select to_char(count(*),'FM999,999,999') from $t"
done

echo "=== foreign-key indexes (23) ==="
psql -q -d "$DB" -v ON_ERROR_STOP=1 -f "$JOB/fkindexes.sql"

echo "=== analyze ==="
psql -q -d "$DB" -c "analyze"

echo "=== size ==="
psql -tAq -d "$DB" -c "select pg_size_pretty(pg_database_size('$DB'))"

"""
Split dsqgen's concatenated output into one file per TPC-DS query, and make the SQL
PostgreSQL-compatible (TASKS.md Tier 2, item 33).

dsqgen emits all 99 queries into a single query_0.sql separated by
`-- start query N in stream S using template queryM.tpl` markers, where M is the TPC-DS
query number. This splits on those markers and names each file by M.

One incompatibility needs rewriting. The templates emit bare `+ 14 days`, which is
Teradata/Netezza syntax; PostgreSQL needs `+ interval '14 days'`. Checked explicitly and
NOT rewritten, because PostgreSQL 15 supports them natively: `rollup`, `grouping()`,
`stddev_samp`.

Usage: python3 scripts/collection/split_tpcds_queries.py <raw_dir> <out_dir>
"""

import re
import sys
from pathlib import Path

START = re.compile(r"^--\s*start query\s+(\d+)\s+in stream\s+(\d+)\s+using template\s+query(\d+[a-z]?)\.tpl",
                   re.IGNORECASE)
END = re.compile(r"^--\s*end query", re.IGNORECASE)
# Teradata/Netezza bare day arithmetic -> PostgreSQL interval literal.
DAYS = re.compile(r"([+-])\s*(\d+)\s+days\b", re.IGNORECASE)


def to_postgres(sql):
    return DAYS.sub(lambda m: f"{m.group(1)} interval '{m.group(2)} days'", sql)


def main():
    raw_dir, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    text = next(raw_dir.glob("query_*.sql")).read_text()

    queries, current, tmpl = {}, None, None
    for line in text.splitlines():
        m = START.match(line.strip())
        if m:
            tmpl, current = m.group(3), []
            continue
        if END.match(line.strip()):
            if tmpl and current:
                queries[tmpl] = "\n".join(current).strip()
            tmpl, current = None, None
            continue
        if current is not None:
            current.append(line)
    if tmpl and current:
        queries[tmpl] = "\n".join(current).strip()

    rewritten = 0
    for tmpl, sql in sorted(queries.items(), key=lambda kv: int(re.sub(r"\D", "", kv[0]))):
        fixed = to_postgres(sql)
        if fixed != sql:
            rewritten += 1
        (out_dir / f"ds{tmpl}.sql").write_text(
            f"-- TPC-DS query {tmpl}, SF1 qualification parameters (dsqgen -QUALIFY Y).\n"
            f"-- Generated from tpcds-kit query_templates/query{tmpl}.tpl via the postgres\n"
            f"-- dialect (a copy of netezza.tpl, whose `limit %d` form PostgreSQL accepts).\n"
            f"-- Bare `+ N days` rewritten to `+ interval 'N days'`.\n"
            + fixed + ("" if fixed.rstrip().endswith(";") else ";") + "\n")
    print(f"wrote {len(queries)} queries to {out_dir}  ({rewritten} needed date rewriting)")
    missing = set(str(i) for i in range(1, 100)) - set(re.sub(r"\D", "", k) for k in queries)
    if missing:
        print(f"NOTE: no template output for query numbers: {sorted(missing, key=int)}")


if __name__ == "__main__":
    main()

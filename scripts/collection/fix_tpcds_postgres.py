"""
Make the remaining TPC-DS queries PostgreSQL-compatible (TASKS.md Tier 2, item 33).

92 of 99 generated queries plan on PostgreSQL 15 unmodified. The 7 that do not fail for
two well-known reasons, both fixed here without changing query semantics:

1. "subquery in FROM must have an alias" (ds2, ds14, ds23, ds49)
   The SQL standard does not require a derived-table alias; PostgreSQL does. An alias is
   added. Purely syntactic -- the subquery is never referenced by name.

2. "column lochierarchy does not exist" (ds36, ds70, ds86)
   These define `grouping(a)+grouping(b) as lochierarchy` in the SELECT list and then
   reference `lochierarchy` inside a CASE expression in ORDER BY. PostgreSQL exposes a
   select alias as a bare ORDER BY term but not inside a larger expression. Every
   reference outside the definition is replaced with the parenthesised expression itself,
   which is what the alias stands for.

Usage: python3 scripts/collection/fix_tpcds_postgres.py <query_dir>
"""

import re
import sys
from pathlib import Path


# A derived table is introduced either by `from (` or, in a comma-separated FROM list, by
# `, (select`. The second form is what ds14 uses. Adding an alias to `, (select ...)` is
# safe even when it turns out to be a scalar subquery in a SELECT list, since an alias is
# legal there too. `in (select ...)` and `exists (select ...)` are never preceded by a
# comma, so they cannot match.
DERIVED = re.compile(r"(?:\bfrom\s*\()|(?:,\s*\(\s*(?=select\b))", re.IGNORECASE)


def add_from_aliases(sql):
    """Alias every unaliased derived table. Uses paren matching, not regex, because these
    subqueries nest several levels deep."""
    out, n, i = sql, 0, 0
    while True:
        m = DERIVED.search(out, i)
        if not m:
            break
        # Walk to the matching close paren.
        depth, j = 1, m.end()
        while j < len(out) and depth:
            if out[j] == "(":
                depth += 1
            elif out[j] == ")":
                depth -= 1
            j += 1
        if depth:
            break
        tail = out[j:]
        # An alias is anything that is not a clause keyword or a comma/paren.
        nxt = re.match(r"\s*([A-Za-z_][A-Za-z_0-9]*)", tail)
        kw = {"where", "group", "order", "limit", "having", "union", "intersect",
              "except", "on", "join", "left", "right", "inner", "outer", "full",
              "cross", "window", "as", "and", "or"}
        needs = not (nxt and nxt.group(1).lower() not in kw)
        if needs:
            n += 1
            out = out[:j] + f" as dt{n}" + out[j:]
            i = j + len(f" as dt{n}")
        else:
            i = j
    return out, n


def inline_select_alias(sql, alias):
    """Replace references to a SELECT-list alias with the expression it names."""
    m = re.search(r"([^\n,]+?)\s+as\s+" + alias + r"\b", sql, re.IGNORECASE)
    if not m:
        return sql, 0
    expr = m.group(1).strip()
    if not (expr.startswith("(") and expr.endswith(")")):
        expr = f"({expr})"
    definition = m.group(0)
    placeholder = "\x00DEF\x00"
    sql = sql.replace(definition, placeholder)
    sql, n = re.subn(r"\b" + alias + r"\b", expr, sql)
    return sql.replace(placeholder, definition), n


def main():
    qdir = Path(sys.argv[1])
    changed, skipped = [], []
    MARKER = "-- PostgreSQL compatibility:"
    for f in sorted(qdir.glob("*.sql")):
        sql = f.read_text()
        # Idempotent: this rewrites syntax in place, so a second pass would alias an
        # already-aliased derived table (`as dt1 as dt1`) and break the query.
        if MARKER in sql:
            skipped.append(f.stem)
            continue
        orig = sql
        notes = []

        sql, n_alias = add_from_aliases(sql)
        if n_alias:
            notes.append(f"aliased {n_alias} derived table(s)")

        for alias in ("lochierarchy",):
            if re.search(r"\bas\s+" + alias + r"\b", sql, re.IGNORECASE):
                sql, n = inline_select_alias(sql, alias)
                if n:
                    notes.append(f"inlined {n} `{alias}` reference(s)")

        if sql != orig:
            header = MARKER + " " + "; ".join(notes) + ".\n"
            f.write_text(header + sql)
            changed.append((f.stem, "; ".join(notes)))

    total = len(list(qdir.glob("*.sql")))
    print(f"modified {len(changed)} of {total} queries"
          + (f" ({len(skipped)} already fixed, skipped)" if skipped else ""))
    for q, n in changed:
        print(f"  {q}: {n}")


if __name__ == "__main__":
    main()

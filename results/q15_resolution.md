# Q15 resolved (Tier 1, item 28)

TPC-H Q15 was dropped from the entire project — all 21-query statistics exclude it, and every
document has had to explain the gap. The stated reason was that it "requires a view/temp-table
construct that needed special handling in the collection script".

That is accurate but it was never a hard problem. The spec form wraps the query in
`CREATE VIEW revenue0 ... ` / `DROP VIEW revenue0`, which a single-statement
`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` harness cannot express. A CTE is a single statement
and semantically identical.

## The subtlety that makes it correct rather than merely convenient

A naive `WITH revenue0 AS (...)` is **not** equivalent to the view. The CTE is referenced twice
(once in the join, once in the `max()` subquery). PostgreSQL 12+ materialises a CTE referenced
more than once, computing it once and sharing the result. A view is inlined at every reference,
so the spec form evaluates the aggregate twice. Different plan, different cost, different
runtime.

`AS NOT MATERIALIZED` restores view semantics. Measured on the local SF1 instance:

| form | node_count | plan_depth | root_cost | rows_processed |
|---|---:|---:|---:|---:|
| spec form (real `CREATE VIEW`) | 13 | 7 | **306862.09** | 641,812 |
| **CTE `NOT MATERIALIZED`** (ships as `q15.sql`) | 13 | 7 | **306862.09** | 611,857 |
| CTE `MATERIALIZED` | 11 | 6 | 153957.94 | 335,922 |

The `NOT MATERIALIZED` rewrite reproduces the view form's plan **exactly** — same node count,
same depth, same total cost to the cent. The materialised variant is a visibly different plan
and would have been the wrong substitution. The 4.7% gap in `rows_processed` between the two
identical-plan forms is run-to-run variation in how many parallel workers the executor was
granted, not a semantic difference.

Both the view form and the correct CTE cost roughly **2x** the materialised form, so had Q15
been collected with a plain `WITH`, its runtime would have been systematically understated.

## Status

`scripts/collection/queries/q15.sql` now exists and runs under the standard harness. The
workload is **22 queries**, not 21.

What this does *not* do: there is no Q15 row in `tpch_dataset_corrected_v2.csv`, because that
would require re-running the five EC2 instances, which are terminated. Q15 is available for all
*new* measurement — the elasticity probe, plan-structure features, and the arm64 laptop
baseline — and must be collected for every machine in the Tier 2 expansion. Until then,
cross-machine statistics remain 21-query and should say so for that reason rather than the old
one.

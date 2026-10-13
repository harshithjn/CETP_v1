# TPC-H queries — reconstructed 2026-10-12

These 21 `.sql` files are **in-repo on purpose.** `collect_query.py` and
`extract_plan_features.py` previously pointed at
`/Users/harshithj/Main/Archive/OtherFiles/patched_queries`, outside version control. That
directory no longer exists, so the exact query text behind the entire dataset was lost. These
files are a reconstruction, validated against the artifacts the originals left behind.

**Never move these out of the repo again.** The query text is as much a part of the dataset as
the timings.

## Validation method

The local `tpch` database is byte-exact standard SF1 on every table (lineitem 6,001,215;
orders 1,500,000; customer 150,000; part 200,000; partsupp 800,000; supplier 10,000;
nation 25; region 5), and `results/plan_structure_features.csv` was generated from it with the
*original* queries. That file therefore fingerprints each original query: output cardinality,
node count, plan depth, total cost and summed actual rows.

Each reconstruction was checked against all five. Results: `results/query_reconstruction_validation.csv`.

| Check | Result |
|---|---|
| Plan shape (`node_count` + `plan_depth`) exact | **21 / 21** |
| Output cardinality (`root_actual_rows`) exact | **19 / 21** |
| `rows_processed` within 1% | 15 / 21 (within 5%: 20 / 21) |
| Verdict | **19 CONFIRMED, 2 APPROXIMATE** |

Residual drift in `rows_processed` and `root_cost` is the ANALYZE/vacuum-history effect already
documented in `results/buffer_bug_correction_report.md` — cost estimates and per-node actual row
counts move with statistics and with how many parallel workers the executor happens to get.
Output cardinality does not, which is why it is the primary fingerprint.

## Two parameters recovered by search, not assumption

- **q11 uses `KENYA`, not the textbook `GERMANY`.** At the spec fraction 0.0001, GERMANY yields
  1048 rows; the original yielded 1217. Sweeping all 25 nations, KENYA gives exactly 1217 — and
  then `rows_processed` (125,013), `node_count` (15), `plan_depth` (6) and `root_cost`
  (10,534.80) all match to the digit. Unambiguous.
- **q18 uses `having sum(l_quantity) > 314`,** not 300. 300 yields 57 rows; the original yielded
  9. Thresholds 313–316 all yield 9 and are indistinguishable (the constant sits in a HAVING, so
  the plan is identical); 314 was chosen as the midpoint of the spec's 312–315 range. All five
  fingerprint fields match exactly.

## Two queries that remain APPROXIMATE — know this before using them

| query | original | reconstruction | gap |
|---|---|---|---|
| **q16** | 18,141 rows | 18,314 rows | **+0.95%** |
| **q20** | 193 rows | 186 rows | **−3.63%** |

Both have the correct structure and an exactly matching plan shape, and q20's total cost matches
to 0.00%. Only a substitution constant differs.

- **q16**: the excluded brand and/or type prefix differs. An analytic sweep of all 25 brands ×
  30 spec type-prefixes against the standard size set `(49,14,23,45,19,3,36,9)` produced **no**
  pair yielding 18,141 — so the eight-element size set itself must differ, and C(50,8) is too
  large to search.
- **q20**: the colour and/or nation differs. 15 (nation, colour) pairs from a 32-word colour
  list yield exactly 193 rows and all give the identical plan and cost, but none reproduces the
  original's `rows_processed` of 5,426,542 (closest: INDIA/peru at +0.42%, IRAQ/navy at −0.56%).
  TPC-H builds `p_name` from 92 colour words; the true one is outside the list tried.

**Consequence:** any *new* measurement on q16 or q20 — elasticity, plan features, laptop-as-dev
timings — describes a query very slightly more selective or less selective than the one in
`tpch_dataset_corrected_v2.csv`. Plan shape, join structure and data volume are preserved, so
resource-elasticity conclusions carry over; absolute runtimes for these two do not. Every
derived artifact must carry the flag.

## Reproducing the validation

```bash
python3 scripts/collection/validate_queries.py
```

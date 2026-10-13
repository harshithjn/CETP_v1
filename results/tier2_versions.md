# PostgreSQL version sensitivity (Tier 2, item 36)

The same 22 TPC-H queries on PostgreSQL 14, 15, 17, 18, over byte-identical data (moved by `pg_dump`, so only the engine differs). Tier 3 showed the plan is hardware-dependent; this asks whether it is version-dependent too — which matters because the entire EC2 dataset comes from a single version.

**12 of 22 queries get a different plan on at least one version.**

Runtime relative to PostgreSQL 15 (median over queries):

| version | median runtime ratio | queries with a different plan vs 15 |
|---|---:|---:|
| 14 | 0.796x | 4/22 |
| 15 | 1.000 (reference) | — |
| 17 | 0.708x | 2/22 |
| 18 | 0.740x | 9/22 |

## Per query

| query | t14 (ms) | t15 (ms) | t17 (ms) | t18 (ms) | distinct plans |
|---|---:|---:|---:|---:|---:|
| q1 | 1282 | 1813 | 1252 | 1173 | 1 |
| q10 | 389 | 448 | 476 | 262 | 4 |
| q11 | 140 | 174 | 92 | 475 | 2 |
| q12 | 428 | 580 | 343 | 301 | 1 |
| q13 | 1156 | 1313 | 455 | 430 | 2 |
| q14 | 241 | 358 | 256 | 180 | 2 |
| q15 | 531 | 831 | 418 | 409 | 1 |
| q16 | 240 | 374 | 249 | 267 | 1 |
| q17 | 725 | 1070 | 693 | 655 | 1 |
| q18 | 1709 | 2964 | 1620 | 2022 | 2 |
| q19 | 63 | 75 | 53 | 54 | 1 |
| q2 | 1147 | 1852 | 1126 | 1406 | 1 |
| q20 | 1014 | 1479 | 992 | 1271 | 2 |
| q21 | 1246 | 1501 | 1140 | 1314 | 2 |
| q22 | 45 | 57 | 51 | 50 | 2 |
| q3 | 575 | 345 | 246 | 346 | 2 |
| q4 | 144 | 155 | 115 | 153 | 1 |
| q5 | 386 | 418 | 382 | 504 | 1 |
| q6 | 244 | 323 | 273 | 175 | 1 |
| q7 | 2166 | 2580 | 2342 | 3013 | 2 |
| q8 | 309 | 340 | 319 | 341 | 2 |
| q9 | 1582 | 1750 | 1427 | 1819 | 2 |

## Why this matters to the project

The hardware signature is supposed to capture *the machine*. If a major version change alters plans and runtimes on identical hardware and identical data, then 'production' is a pair — hardware **and** engine version — and a two-scalar hardware signature cannot express it. Any deployment comparing a dev box to a production server on a different PostgreSQL version is measuring both effects at once and attributing all of it to hardware.

## Limitations

- One machine, so this isolates the engine cleanly but says nothing about version x hardware interaction.
- Default configuration per version, so some of the difference is changed *defaults* rather than changed planner logic. Separating those would need each version run with the others' settings explicitly pinned.
- `pg_restore` moves data but statistics are rebuilt by `analyze` on each version, so small cost differences may be sampling noise in the statistics rather than planner changes.


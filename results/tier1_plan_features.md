# Do plan-structure features help? (Tier 1, item 25)

40 plan-structure features added to the dev-side descriptor: operator-family counts, row volumes and cost shares, plan depth, spills, degree of parallelism, and mean cardinality misestimation.

**LOQO is the headline.** Under leave-one-machine-out every query is in the training set and, because all six existing dev-side features are machine-invariant, the model can memorise per-query scaling — richer query features cannot help a model that has already memorised the query. Plan structure can only pay off on an UNSEEN query.

MAPE % on `scaling_factor_p50`, multi-core compute signal, classifier refit per fold.

| feature set | n features | LOQO (new query) | LOMO (new machine) |
|---|---:|---:|---:|
| hw_only | 2 | 16.10 | 25.45 |
| base | 13 | 17.29 | 23.90 |
| base+plan | 53 | 13.86 | 23.33 |
| plan_only | 42 | 13.29 | 24.77 |

## Verdict

**Plan structure helps on unseen queries**: adding it improves LOQO by 3.44pp (17.29% -> 13.86%). This is the direction to invest in — and it is the argument for a tree-structured encoder once Tier 2 supplies enough queries to train one.

`hw_only` (16.10% LOQO) is the floor: two hardware ratios and nothing about the query at all. Compare every other row against it — any feature set that fails to beat it is contributing nothing.

## Caveats

- Plan features were extracted on the **local Apple M1 instance**, not on the EC2 machines, which are terminated. Plan *shape* is a property of the query and the data and transfers; `dop` and `workers_launched` are **not** — they depend on the machine's core count. Those two columns describe the M1, not the production machine, and are a known source of mismatch.
- Being machine-invariant, these features cannot break the query-memorisation problem under LOMO. They change what the model knows about a query, not what it knows about a machine.
- 0 pairs were dropped for missing plan features (q15 has no row in the cross-machine dataset).


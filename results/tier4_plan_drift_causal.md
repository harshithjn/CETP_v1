# Causal plan-drift decomposition (item 3)

Plans forced with `pg_hint_plan` so the decomposition is **causal and exact**, not an association:

```
total    = T(B,P_B) / T(A,P_A)     what a cross-environment predictor must explain
plan     = T(B,P_B) / T(B,P_A)     pure plan effect,     settings held at B
settings = T(B,P_A) / T(A,P_A)     pure settings effect, plan held at A
total    = plan x settings         identically
```

Over **30 cells** where the two environments genuinely chose different plans and the forced plan verified (41% of 74 differing-plan cells). The identity holds to a max log residual of 2.4e-16, so this is an exact decomposition rather than a fit.

## Result: when the plan changes, the plan is the dominant term

| effect | geometric-mean runtime ratio |
|---|---:|
| total (observed across environments) | 1.672x |
| pure **plan** effect | **1.673x** |
| pure **settings** effect | **1.145x** |

**Mean share of the log-runtime change attributable to the plan: 70.5%.**

| pair | n | total | plan | settings | plan share |
|---|---:|---:|---:|---:|---:|
| mid->cores | 4 | 1.311x | **1.353x** | 1.204x | 51.6% |
| mid->cores_nvme | 4 | 1.344x | **1.321x** | 1.121x | 58.5% |
| mid->nvme | 6 | 1.492x | **1.437x** | 1.055x | 68.4% |
| mid->serial | 12 | 2.115x | **2.197x** | 1.208x | 80.7% |
| serial->cores | 4 | 1.555x | **1.450x** | 1.073x | 74.3% |

## This reverses Tier 3's conclusion, and both numbers are right

Tier 3 measured plan drift observationally and found ~16% of cross-environment variation *associated* with plan change, concluding that drift was the **minor** term. The causal measurement says the opposite: **70.5%**.

The two are not in conflict -- they condition differently:

- Tier 3 averaged over **all** environment pairs, including the majority where both environments chose the *same* plan and the plan effect is necessarily zero. That dilutes the estimate toward zero.
- This decomposition conditions on the cells where the plan **did** change, which is the only place a plan effect can exist.

Put together, the honest statement is: **the plan changes often (20/22 queries across environments, 55% of queries when core count alone moves), and when it changes it accounts for about 70% of the runtime difference.** Plan divergence is therefore a first-order mechanism in cross-environment prediction, not a refinement -- which is the opposite of what Tier 3 concluded and supersedes it.

## What it means for the research direction

Every cross-hardware cost model, including this one, learns a mapping from a plan to a runtime and then applies it to a machine where **that plan would not have been chosen**. If roughly 70% of the error in those cases is the plan rather than the hardware, then the better question is not *"how long will this plan take over there"* but *"which plan will be chosen over there"* -- a different and more tractable problem, because the planner is a deterministic function of statistics and settings rather than a noisy physical process.

`RELATED_WORK.md` found no formal treatment of this. COSTREAM (ICDE 2024), the closest cross-hardware cost model, targets streaming operator placement where no cost-based optimiser enumerates join orders, so the question cannot arise there. This appears to be the first causal decomposition of cross-environment query runtime into planner choice versus execution capability.

## Limitations, and one that matters a lot

- **Selection bias.** Forcing verified on only 41% of differing-plan cells. The failures are not missing at random: they are the plans whose shape pg_hint_plan cannot express (aggregate strategy, some InitPlan/SubPlan forms, certain parallel degrees). So this conditions on *forceable* plans, which are plausibly the structurally simpler ones. **The 70.5% is an estimate over a biased subsample**, and that is the single biggest caveat on it.
- **One machine.** Environments are planner *beliefs* (`max_parallel_workers_per_gather`, `random_page_cost`), not different silicon. That is what makes capability constant and the isolation clean, but the `settings` term here is configuration's effect on execution, not hardware's.
- **`work_mem` is held fixed** at 4MB across every environment, deliberately: it changes the aggregate strategy, which pg_hint_plan cannot force. So this says nothing about memory-driven plan change.
- 22 TPC-H queries; q16 and q20 are approximate reconstructions.

## The first attempt failed, and the failure is instructive

v1 reported a 1.54x plan effect that was **entirely an artifact**. Two bugs, both silent:

1. **`pg_hint_plan` was never LOADed into the session.** `CREATE EXTENSION` registers the SQL objects, but the planner hook lives in the shared library and needs `LOAD 'pg_hint_plan'`. Every hint was silently ignored, so forcing verified only on cells where the plan was *already* identical -- 100% of those and **0%** of the cells that mattered. A hint comment also has to be the first thing in the statement; placing it after `EXPLAIN` hides it.
2. **The paired arms were measured in separate sweeps**, so buffer-cache state drifted between them. q20 recorded 13,304 ms against 539 ms for the *same plan* -- a 24x artifact that swamped any real signal. v2 interleaves the arms within one session.

The lesson worth keeping: **an unverified forced plan is not a forced plan.** Checking that the achieved plan signature equals the target is what turned a confident wrong answer into a correct one.


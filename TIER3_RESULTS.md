# Tier 3 results — research

Items 39–46 of [TASKS.md](TASKS.md), executed 2026-10-12. Related work was done **first**, as
item 44 instructs, and it changed the framing before a single experiment ran.

## The short version

Tier 3 was meant to find a headline. Its first pass concluded that three of four research
angles did not survive contact with the data. **A follow-up causal experiment overturned that
for N1**, which is now the strongest finding in the project — see the correction under item 39.
The other three verdicts stand.

| # | Angle | Outcome |
|---|---|---|
| 39 | N1 plan drift | **SUPERSEDED — see the correction below.** Causal measurement makes it the **dominant** term (70.5%), not subordinate |
| 40 | N2 elasticity as descriptor | **Refuted** in Tier 1 — better description, no predictive gain |
| 41 | N3 budgeted cold start | **Works, and inverts the premise**: one well-chosen query beats twelve random ones |
| 42 | N4 conformal risk control | **Machinery works, guarantee voids** on unseen hardware (16/18 budgets missed) |
| 43 | N5 hardware embeddings | **Blocked** — needs 30+ machines |
| 44–46 | Related work | **Done**, and corrected four of eight citations plus found the closest prior work, which the list had missed |

The through-line: **every angle fails for the same reason** — five machines, and a hardware
signature that is effectively one-dimensional. That is now a finding supported from five
independent directions, not a limitation to apologise for.

---

## 39 — N1: plan drift

**The question.** The project assumes workload cost is hardware-invariant and only capability
changes. For a relational optimiser that is false: PostgreSQL reads hardware-derived settings
and picks a different plan, so the dev-side cost vector describes a plan production will not
run. Nobody has decomposed cross-environment error this way (`RELATED_WORK.md`).

**The design.** Measured on **one** machine, varying only the planner's hardware *beliefs*
(`max_parallel_workers_per_gather`, `effective_cache_size`, `work_mem`, `random_page_cost`).
Hardware capability is constant by construction, so every runtime change is plan divergence
with **no capability confound** — an isolation two machines cannot give, since there both
terms move together. 22 TPC-H queries × 6 environments, 264 measurements.

**Result 1 — drift is pervasive.** **20 of 22 queries change plan**; median 3 distinct plans,
max 6. By single axis, from the baseline:

| hardware belief changed | plans changed | runtime ratio when plan changed |
|---|---:|---:|
| `max_parallel_workers_per_gather` 2 → 8 | **12/22 (55%)** | 1.27× |
| `random_page_cost` 4.0 → 1.1 | 9/22 (41%) | 1.39× |
| `work_mem` 4MB → 64MB | 4/22 (18%) | 2.74× |

Core count is the dominant plan-changer — independent corroboration of the parallelism
mechanism, from a completely different measurement.

**Result 2 — but it is the smaller term.** Splitting all environment pairs by whether the plan
signature was identical: same-plan pairs swing runtime **2.16×**, different-plan pairs
**2.49×** (Mann-Whitney p = 1.7×10⁻³). So only **~16%** of cross-environment variation is
attributable to the plan changing. Most of it is the settings' effect on *execution*.

**Result 3 — and the sign is backwards.** Plan-volatile queries are predicted **better**, not
worse: per-query error correlates with the number of distinct DOP levels at **r = −0.551,
p = 0.010**. So volatility cannot be the mechanism behind the error. The coherent reading: a
query whose plan changes with core count is a query the planner can *parallelise*, and
parallelisable queries are exactly what the multi-core compute ratio captures.

**Verdict at the time: N1 should not be the headline.** That verdict is now **wrong**, and
the correction is below.

### CORRECTION (2026-10-13): N1 *is* the headline

The observational estimate above was diluted. A causal decomposition using forced plans
(`results/tier4_plan_drift_causal.md`) finds that **when the plan actually changes, plan
divergence accounts for 70.5% of the runtime difference** — the pure plan effect is 1.673x
against 1.145x for the pure settings effect, over 30 cells where forcing verified.

Both numbers are correct; they condition differently. The ~16% above averages over **all**
environment pairs, including the majority where both environments chose the *same* plan and a
plan effect cannot exist. The 70.5% conditions on the cells where the plan **did** change.

Put together: **the plan changes often (20/22 queries; 55% when core count alone moves), and
when it changes it explains about 70% of the runtime difference.** Plan divergence is a
first-order mechanism, not a refinement. The research question it implies — *which plan will
production choose* rather than *how long will this plan take there* — is both more tractable
(the planner is deterministic) and unaddressed in the literature.

One caveat does real damage to the precision of the 70.5%: forcing verified on only **41%** of
differing-plan cells, and the failures are not random — they are plans whose shape
`pg_hint_plan` cannot express. The estimate is over a biased, plausibly simpler subsample.

The inverted correlation (r = −0.551) still stands and is still interesting: plan-volatile
queries are predicted *better*, because volatility marks parallelisability.

## 41 — N3: budgeted cold start

Post-calibration MAPE, leave-one-machine-out, scored only on queries **not** revealed:

| k | random | spread | dopt | elasticity | oracle |
|---:|---:|---:|---:|---:|---:|
| 1 | 19.51 | **13.20** | 14.45 | 13.20 | 12.70 |
| 3 | 15.82 | 12.57 | 13.78 | 15.82 | 12.44 |
| 12 | 13.45 | 12.25 | 13.22 | 14.05 | 5.76 |

Uncalibrated baseline: **15.84%**.

- **One well-chosen measurement beats twelve arbitrary ones.** `spread` at k=1 (13.20%) beats
  `random` at k=12 (13.45%). *Which* queries you run matters more than how many — the
  opposite of `addition7_online_loop.py`'s arbitrary 12, and a far cheaper operating point.
- **Calibrating from a single random query makes things worse** (19.51% vs 15.84%
  uncalibrated). One query is a noisy estimate of the machine correction, and a bad correction
  is worse than none. A loop revealing queries in arbitrary order can *degrade* the predictor
  before improving it — undetectable in the previous design, which never measured k=1.
- **`elasticity` and `dopt` get worse as k grows.** Both chase *extremes*; for estimating a
  single scalar you want *representative* queries. `spread` in predicted-scaling space — the
  only objective aligned with the quantity being estimated — is the most stable column.
- **The gain is modest overall** (15.84% → ~12%), consistent with Tier 1: the analytical
  formula is already close to per-machine unbiased, so little systematic correction remains.
  Worth doing because it is nearly free, not because it transforms anything.

## 42 — N4: conformal risk control

A deployment owner states a budget — "no more than 5% of what I approve may breach" — and the
gate threshold `λ` is *derived* by conformal risk control rather than chosen.

**The machinery works. The guarantee does not survive an unseen machine: 16 of 18
configurations exceed their stated budget on the held-out machine**, in the worst cases by
several times. At τ=1.5 with a 5% budget, `λ=1.00` approves 92.5% of candidates and realises
8.33% risk, worst fold 26.8%.

This is not an implementation defect. CRC guarantees its bound **under exchangeability**, and
a held-out machine is not exchangeable with the training machines — the c5a finding again.
Tier 1 reached the same conclusion from the coverage side (97.0% marginal at nominal 95%, but
90.5% on the worst machine). Two independent formulations, same answer.

**The gap is exchangeability, which is a data problem, not a method problem.** Keep the
framing — it beats the shipped gate's hand-set 0.6, which Tier 1 showed was not even estimable
at n=5 — but report a stated risk budget on unseen hardware as *aspirational*, and lean on
abstention instead.

## 43 — N5: blocked

Learned hardware embeddings need 30+ machines to factorise a machine × query matrix. Tier 1
already answered the underlying question negatively by a cheaper route: with two axes
correlated at r = +0.958 and 97.9% of variance on one component, there is no second dimension
to embed. Revisit after Tier 2.

## 44–46 — Related work

Full detail in [RELATED_WORK.md](RELATED_WORK.md). Two things worth surfacing here.

**Four of eight citations were wrong or incomplete.** "QPPNet" is not the paper's own name;
the covariate-shift conformal paper is Tibshirani, **Barber, Candès & Ramdas** (three
co-authors were being dropped); Ganapathi's title and Neo's eight-author list were off; the
Roofline volume/issue still needs confirming.

**The list missed the closest prior work.** Heinrich, Binnig, Kornmayer & Luthra, **COSTREAM**
(ICDE 2024) is a learned cost model taking hardware as input that claims generalization to
unseen hardware. I initially recorded it as interpolation-only; that was wrong — its Exp 4
explicitly extrapolates beyond the training range and largely holds up. **So "no prior work
evaluates cross-hardware extrapolation" must not be claimed.** Note Carsten Binnig co-authors
both COSTREAM and the zero-shot cost models paper: the Darmstadt/DFKI group owns both nearest
pieces of prior art and will plausibly review this.

**What survives as novel**, and only this:
1. **Relational OLAP rather than streaming** — and the distinction is substantive, because a
   relational optimiser chooses a different plan on different hardware, which is item 39's
   question. Streaming operator placement has no plan enumerator.
2. **Latency percentiles as the decision variable.** Every paper in the field predicts a point
   cost and reports *q-error* percentiles. None predicts p95/p99 of the latency distribution,
   which is what an SLA is written against.
3. **Abstention with calibrated coverage.** No prior work declines to answer.

## What Tier 3 implies

**The paper to write is a measurement study, not a better predictor.** Every angle above
failed in the same direction, and the failures are coherent and mutually corroborating:

- The hardware signature is one-dimensional (r = +0.958), so no weighting scheme can help —
  confirmed independently by the classifier ablation, the elasticity experiment, and N5 being
  moot.
- The compute signature is a hardware **× configuration** artifact: probed at `nproc` while a
  query can use `1 + max_parallel_workers_per_gather`. Invisible at 2–4 cores, catastrophic at
  8. **Every paper encoding hardware capability as a scalar is exposed to this, and none
  discusses it.**
- Exchangeability across machines fails, so both coverage and risk guarantees void on unseen
  hardware — shown twice, from independent formulations.
- Plan drift is real, driven by core count, and its *inverted* correlation with error is
  itself informative.

That critique generalises beyond CETP, which is what makes it publishable. COSTREAM uses four
hardware features and never tests their independence either.

**Target the honest venue.** aiDM or DBTest at SIGMOD, where a rigorous negative-result
measurement study with a public dataset is in scope. Not a VLDB full paper claiming
state-of-the-art, which this evaluation cannot support and should not attempt.

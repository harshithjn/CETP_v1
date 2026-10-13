# Related work, verified — and how it reframes the contribution

TASKS.md Tier 3, items 44–46. Written **before** the Tier 3 experiments, as item 44
instructs, and it did change the framing.

Every citation below was checked against the publisher or DBLP rather than quoted from
memory. **Four of the eight I was working from were wrong or incomplete**, and the
literature search turned up a paper that is far closer to this project than anything on the
original list.

---

## 1. Corrections to the citation list

| Claimed | Verified | Correction |
|---|---|---|
| Hilprecht & Binnig, Zero-Shot Cost Models, VLDB 2022 | **PVLDB 15(11):2361–2374, 2022**, DOI 10.14778/3551793.3551799, arXiv 2201.00561 | Correct. Also cite the precursor, *One Model to Rule them All: Towards Zero-Shot Learning for Databases*, **CIDR 2022**. |
| "QPPNet (Marcus & Papaemmanouil, VLDB 2019)" | Marcus & Papaemmanouil, *Plan-Structured Deep Neural Network Models for Query Performance Prediction*, **PVLDB 12(11):1733–1746, 2019**, DOI 10.14778/3342263.3342646 | **"QPPNet" is not the paper's own name** and could not be confirmed as such from any primary source. Cite by title. |
| "Neo (Marcus et al., VLDB 2019)" | **PVLDB 12(11):1705–1718, 2019**, DOI 10.14778/3342263.3342644 | Eight authors: Marcus, Negi, Mao, Zhang, Alizadeh, Kraska, Papaemmanouil, Tatbul. |
| "Bao (Marcus et al., SIGMOD 2021)" | **SIGMOD '21, pp. 1275–1288**, DOI 10.1145/3448016.3452838 | Correct. SIGMOD 2021 **best paper**, Data Management track — worth noting when citing. |
| "Ganapathi et al., Predicting Multiple Metrics for Queries, ICDE 2009" | **ICDE 2009, pp. 592–603** | Correct title (not "Multiple *Performance* Metrics", which some listings use). Seven authors, incl. Jordan and Patterson. Won the **ICDE Ten-Year Influential Paper Award**. |
| "Roofline (Williams, Waterman, Patterson, CACM 2009)" | Williams, Waterman & Patterson, *Roofline: An Insightful Visual Performance Model for Multicore Architectures*, **CACM 2009** | Volume/issue **not confirmed** — verify in the ACM DL before submitting. The 2008 tech report (UCB/EECS-2008-134) has a longer title. |
| "Romano CQR, NeurIPS 2019" | Romano, Patterson & Candès, **NeurIPS 2019, pp. 3543–3553**, arXiv 1905.03222 | Correct. |
| "Tibshirani covariate-shift conformal, NeurIPS 2019" | **Tibshirani, Barber, Candès & Ramdas**, *Conformal Prediction Under Covariate Shift*, **NeurIPS 2019, pp. 2530–2540**, arXiv 1904.06019 | **Three co-authors were missing.** Barber and Ramdas in particular should not be dropped. |

## 2. The paper the original list missed, and it is the closest prior work

**Heinrich, Binnig, Kornmayer & Luthra, "COSTREAM: Learned Cost Models for Operator
Placement in Edge-Cloud Environments," ICDE 2024, pp. 1–14** (arXiv 2403.08444).

This is a learned cost model that takes **hardware as model input** and claims to
"generalize to unseen placements, queries, and hardware." That is, on its face, this
project's thesis. It had to be read rather than skimmed. Having read it:

| | COSTREAM | CETP |
|---|---|---|
| Workload | **Streaming** (Apache Storm + Kafka): filters, windowed aggregations/joins | **Relational OLAP** (PostgreSQL): TPC-H / TPC-DS / JOB plans |
| Hardware encoding | 4 features: CPU %, RAM MB, network latency, network bandwidth | 2 scalars: STREAM bandwidth, multi-core compute |
| Hardware generalization tested | **Both.** Exp 3 interpolates within the training range (Q50 1.37–1.59); **Exp 4 extrapolates beyond it** (Q50 mostly 1.4–2.8; worst case processing latency Q50 6.09, Q95 406.83) | Extrapolation — leave-one-machine-out, incl. an outlier machine outside the hull |
| Target statistic | Processing / end-to-end latency, MSLE loss. Reports q-error **Q50/Q95** — percentiles *of the error*, not of latency | **Latency percentiles themselves** (p50/p95/p99) for an SLA decision |
| Uncertainty | Ensemble vote + candidate filtering. **No confidence or abstention** | Hardware-distance abstention; conformal coverage guarantees |

**Correction worth recording:** on a first pass I noted COSTREAM as interpolation-only and
treated "nobody leaves the hull" as this project's opening. That was wrong — Exp 4 explicitly
tests hardware beyond the training range and largely holds up, degrading most on extrapolated
network latency. The claim "no prior work evaluates cross-hardware extrapolation" must not be
made. What survives is narrower and listed in §4.

**Note that Carsten Binnig co-authors both COSTREAM and the zero-shot cost models paper.**
The TU Darmstadt / DFKI group owns the two nearest pieces of prior art, and will plausibly
review this work. Position against them explicitly and generously.

Also newly found and worth citing:

- **A Learned Cost Model-based Cross-engine Optimizer for SQL Workloads** (arXiv 2506.02802,
  2025) — transfers across *engines* with one predictor head per engine and provisioning.
  Cross-engine, not cross-hardware; does not address unseen configurations.
- **RegRes** (*Applied Sciences*, 2025, DOI 10.3390/app16020910) — cross-platform performance
  prediction **Intel x86 → ARM M1** with transfer learning; tree models (Extra Trees,
  R² ≈ 0.92) beat DNNs under data scarcity. Not a database paper, but it is the closest thing
  to Tier 1's item-27 experiment and should be cited there.
- **Akdere et al., Learning-based Query Performance Modeling and Prediction** (Brown) — the
  classic plan-level QPP baseline.
- The cross-hardware cost-model problem is actively studied in **tensor compilers**, not
  databases: **TLP** (arXiv 2211.03578), **Moses** (arXiv 2201.05752), **TCL**. TLP states the
  problem precisely — "data collected offline are invalid across hardware caused by domain
  gaps in hardware architecture and hardware performance." Citing this adjacent literature
  shows the problem is recognised and that the database side is the gap.

## 3. The landscape, in one table

| Work | Transfers across | Holds fixed | Predicts |
|---|---|---|---|
| Ganapathi 2009, Akdere | — | hardware, database | multiple metrics, point |
| Plan-structured DNN 2019; Neo; Bao | queries / plans | hardware, database | latency; plan choice |
| Zero-shot cost models 2022 | **databases / schemas** | **hardware** | cost, point |
| Cross-engine optimizer 2025 | **engines** | hardware per head | cost, point |
| COSTREAM 2024 | **hardware (interpolation)** | workload class (streaming) | latency, point |
| TLP / Moses / TCL | **hardware** (tensor programs) | not SQL | kernel latency |
| **CETP** | **hardware (extrapolation)** | database, SF, engine | **p50/p95/p99 for an SLA decision, with abstention** |

## 4. The differentiator, stated honestly (item 46)

The sentence the task list proposed — "they transfer across databases predicting mean
runtime; you transfer across hardware, predict percentiles for an SLA decision, and
abstain" — is **correct for the zero-shot line but incomplete**, because COSTREAM already
transfers across hardware. The defensible version:

> Learned cost models transfer across **databases** (zero-shot cost models, VLDB 2022) or
> across **engines** (cross-engine optimizer, 2025) while holding hardware fixed. COSTREAM
> (ICDE 2024) does take hardware as input and does evaluate extrapolation beyond its training
> range — but for **streaming** operator placement, where no cost-based optimizer is
> enumerating join orders, and it predicts a **point** latency with no notion of
> uncertainty. CETP asks the complementary question for **relational OLAP** queries, where
> the plan itself is chosen by a hardware-sensitive optimizer: can production runtime be
> predicted as a **percentile** an SLA can be written against, with an explicit
> **abstention** when the answer should not be trusted?

Three things, and only these three, are defensible as novel:

1. **Relational OLAP rather than streaming.** The distinction is not cosmetic: a relational
   optimizer *chooses a different plan* on different hardware, which is the N1 question
   (item 39). Streaming operator placement has no plan enumerator, so the problem cannot
   arise there.
2. **Latency percentiles as the decision variable.** Every paper in the table predicts a
   point cost and reports *q-error* percentiles. None predicts p95/p99 of the latency
   distribution, which is what an SLA is actually written against.
3. **Abstention with calibrated coverage.** No paper in the table declines to answer.

Anything stronger is not supportable, and a reviewer from the Darmstadt group would catch it
immediately.

## 5. What reading this first actually changed

Item 44 said related work would change the framing. It did, in three ways.

**(a) "Better predictor" is the wrong claim to make.** Every paper above reports a model that
wins. This project's Tier 1 result is that its own learned model **only interpolates**
(17.29% MAPE with all machines seen, 26.86% with the target machine held out) and that a
one-parameter formula beats it out of the hull. Framing CETP as a better predictor invites
comparison on a metric where a 5-machine study cannot win. Frame it as a **measurement
study of why cross-hardware transfer is hard**. Note that COSTREAM's extrapolation results
*corroborate* rather than contradict this: its accuracy also degrades outside the hull, worst
on the dimension it encodes least well. Two independent studies finding the same failure mode
is a stronger story than either alone.

**(b) The strongest findings are the negative and structural ones.** Specifically:
- A two-scalar hardware signature is **one-dimensional** in practice (r = +0.958, 97.9% of
  variance on one component), and that axis is largely core count (r = +0.841). COSTREAM uses
  four hardware features and never reports whether they are independent either. **This
  critique generalizes beyond CETP**, and that makes it publishable.
- The compute signature is a hardware **× configuration** artifact: probing at `nproc` while
  a query can use only `1 + max_parallel_workers_per_gather` is a definition bug, invisible at
  2–4 cores and catastrophic at 8 (324% → 153% MAPE on correction). Any paper encoding
  "hardware capability" as a scalar is exposed to this, and none of them discuss it.
- Hand-assigned bottleneck classes are not supported by measured resource elasticity, and a
  uniform weighting beats the learned classifier.

**(c) The percentile + abstention angle is the real novelty, and it is under-built, not
over-claimed.** No paper in the table predicts latency *percentiles* as the decision
variable, and none abstains. Tier 1 showed conformal prediction delivers 97.0% empirical
coverage at nominal 95% where the shipped quantile models give 84.6%. That is a complete,
defensible contribution **today**, at n=5 machines, and it does not depend on Tier 2 landing.
It is also the piece most directly connected to the product framing.

## 6. Reading still outstanding

- **Read the COSTREAM full text properly** (the PDF, not the HTML extract used here). Its
  Exp 4 is the direct comparator for every CETP extrapolation claim, and its Table V column
  ordering was inferred rather than read, so the per-dimension numbers quoted above need
  confirming before they go in a paper.
- Check whether Heinrich/Binnig/Luthra have a **2025–2026 follow-up** extending COSTREAM to
  relational workloads. If they do, this project's gap narrows sharply and the framing must
  change again. This is the single highest-value check remaining.
- Confirm the **Roofline CACM volume/issue**.
- Find the canonical citation for **JOB** (Leis et al., *How Good Are Query Optimizers,
  Really?*, PVLDB 2015) — required once JOB is in the workload.
- Look for prior work on **plan divergence across environments** specifically, before
  committing to the N1 framing in item 39. If the planner-drift decomposition has been done,
  N1 is not the headline.

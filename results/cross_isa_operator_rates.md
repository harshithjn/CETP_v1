# Does crossing an ISA change the relative cost of database operators?

**No. And that refutes the stated motivation for per-operator capability modelling.**

Measured with `scripts/collection/operator_probe.py` (reps=9, both records passing the
repeatability gate) on:

| | dev | prod |
|---|---|---|
| machine | Apple M1 | AMD EPYC 9V74 |
| ISA | **arm64** | **x86_64** |
| cores | 8 | 4 |
| PostgreSQL | 15.17 (Homebrew) | 15.19 (Debian, `postgres:15`) |
| provenance | laptop | GitHub Actions runner, `.github/workflows/x86-operator-probe.yml` |

## The measurement

| axis | M1 (arm64) tuples/s | EPYC (x86_64) tuples/s | ratio |
|---|---:|---:|---:|
| seqscan | 11,487,849 | 8,753,444 | 1.312 |
| hashjoin | 4,187,714 | 3,784,817 | 1.106 |
| sort | 4,610,457 | 4,000,748 | 1.152 |
| hashagg | 3,458,930 | 2,317,419 | 1.493 |
| nestloop | 842,971 | 770,706 | 1.094 |
| parallel_speedup | 2.625 | 2.211 | 1.187 |

**Cross-ISA operator-ratio spread: max/min = 1.365×.**

## Why this is a negative result

A per-machine **scalar** model multiplies dev runtime by one number. It is wrong exactly to
the extent that the true ratio differs *between operators* — if every axis scaled by the same
factor, a scalar would be sufficient and per-operator modelling would be pointless.

The argument for per-operator rates, as stated earlier in this project, was that an ISA
boundary changes the *relative* cost of operators, so cross-ISA needs per-query capability
information. **That argument does not survive measurement:**

| | operator-ratio spread |
|---|---:|
| arm64 → x86_64 (different ISA, different vendor, different core count) | **1.365×** |
| worst pair *within* the arm64 container grid (`c8m1g` → `c1m3g`, same ISA, same host) | **1.371×** |

**Crossing the ISA boundary perturbs the operator mix no more than changing a CPU quota on
one machine does.** So per-operator weighting cannot be expected to rescue cross-ISA
prediction, and the 59.7% irreducible floor measured on laptop → EC2
([laptop_to_ec2.md](laptop_to_ec2.md)) is **not** caused by operator-mix divergence.

This is consistent with the grid ablation, which already found per-operator rate weighting
worth only −0.08 pp (CI [−0.73, +0.44]) against a single scalar carrying the same parallelism
information. Two independent experiments now say the same thing: **per-operator *rates* are
not the useful part of this model. The parallelism term and its plan-structure localisation
are.**

## What must therefore explain the cross-ISA floor

Not the ISA, and not operator mix. The remaining candidates, in order of evidence:

1. **Storage and buffer-pool regime.** The EC2 instances were EBS-backed (network storage);
   the laptop is local NVMe. Over-prediction in laptop → container already correlated with
   per-query I/O fraction (Spearman r = +0.302), and the probe has no axis that measures the
   storage path — the `io_seqscan` axis built for it tracks CPU quota (+0.762) over memory
   (+0.548) and so does not isolate storage.
2. **Per-query variance that no per-machine quantity can express.** On EC2 the scaling factor
   spans 18–43× *within a single machine* across queries. Per-operator rates are still a
   per-*machine* quantity; they reweight by plan composition but cannot represent per-query
   effects of that magnitude.
3. **Engine build and configuration differences** (Homebrew vs Debian builds, and an inferred
   rather than measured `max_parallel_workers_per_gather` on EC2).

## Scope

- **One x86 machine, one arm64 machine.** Two machines cannot establish a distribution; this
  refutes a strong claim ("ISA changes operator mix substantially") rather than establishing
  its converse with confidence.
- A CI runner is shared hardware. The record passed the repeatability gate (bootstrap SE of
  the median, all axes ≤ 0.08) but its neighbours are not controlled.
- The comparison is signature-level. It says the *mechanism* proposed for beating the floor is
  absent; the runtime-prediction test on the same pair is reported separately once the
  workload collection completes.

"""
Concurrency and PostgreSQL-version sensitivity (TASKS.md Tier 2, items 35 and 36).

Every measurement in this project before now was single-query-at-a-time on one PostgreSQL
version. Both are assumptions the production claim depends on and neither had been tested.

item 35  Runtime under N simultaneous clients, N in {1,2,4,8}. Yields a per-query
         concurrency elasticity d log(runtime) / d log(clients), and -- as a free sanity
         check -- whether the chosen PLAN changes with concurrency. It should not:
         concurrency is not a planner input. If a signature moves, something else did.

item 36  The same 22 queries on PostgreSQL 14, 15, 17 and 18 over byte-identical data
         (moved by pg_dump, so only the engine differs). Tier 3 showed the plan is
         hardware-dependent; this asks whether it is also VERSION-dependent, which matters
         because the project's entire dataset comes from one version.

Writes results/tier2_concurrency.md, results/tier2_versions.md and their CSVs.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from canonical_evaluation import RESULTS_DIR  # noqa: E402

PROJECT_DIR = Path(__file__).resolve().parents[2]
LAPTOP_DIR = PROJECT_DIR / "data" / "laptop"
ELASTICITY = RESULTS_DIR / "tier1_elasticity.csv"


def loglog_slope(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = (x > 0) & (y > 0)
    if len(np.unique(x[ok])) < 2:
        return np.nan
    return float(np.polyfit(np.log(x[ok]), np.log(y[ok]), 1)[0])


def load_many(pattern):
    frames = []
    for f in sorted(LAPTOP_DIR.glob(pattern)):
        d = pd.read_csv(f)
        if "is_warmup" in d.columns:
            d = d[~d["is_warmup"]]
        frames.append(d[d["ok"]] if "ok" in d.columns else d)
    return pd.concat(frames, ignore_index=True) if frames else None


def concurrency_report():
    df = load_many("tpch_m1_c*.csv")
    if df is None or df.empty:
        print("no concurrency data; skipping item 35")
        return
    # Median over repetitions AND clients: each client is an independent observation of
    # "runtime of this query while N of them run at once".
    cell = (df.groupby(["query_id", "concurrency"])
            .agg(time_ms=("time_ms", "median"),
                 time_p95=("time_ms", lambda x: float(np.percentile(x, 95))),
                 n_obs=("time_ms", "size"),
                 dop=("dop", "median"),
                 n_signatures=("plan_signature", "nunique"))
            .reset_index())
    cell.to_csv(RESULTS_DIR / "tier2_concurrency.csv", index=False)

    rows = []
    for q, sub in cell.groupby("query_id"):
        s = sub.sort_values("concurrency")
        base = s[s["concurrency"] == 1]["time_ms"]
        rows.append({
            "query_id": q,
            "e_concurrency": loglog_slope(s["concurrency"], s["time_ms"]),
            "t_c1_ms": float(base.iloc[0]) if len(base) else np.nan,
            "slowdown_c8": (float(s[s["concurrency"] == 8]["time_ms"].iloc[0]
                                  / base.iloc[0]) if len(base)
                            and (s["concurrency"] == 8).any() else np.nan),
            "plans_across_concurrency": int(sub["n_signatures"].max()),
        })
    per_q = pd.DataFrame(rows).sort_values("e_concurrency", ascending=False)

    # Throughput: N clients finishing in T means N/T queries per unit time.
    tput = cell.assign(tput=cell["concurrency"] / (cell["time_ms"] / 1000.0))
    tp = tput.groupby("concurrency")["tput"].sum()

    drift = int((per_q["plans_across_concurrency"] > 1).sum())
    e = per_q["e_concurrency"].dropna()

    L = ["# Concurrency sensitivity (Tier 2, item 35)", "",
         "Every measurement in this project before now was single-query-at-a-time. Production "
         "is not. This measures runtime under N simultaneous clients of the same query, "
         "N in {1,2,4,8}, on the Apple M1 (8 cores).", "",
         "`e_concurrency` is the log-log slope of runtime against client count. 0 means perfect "
         "scaling (the machine absorbs the extra load for free); **1 means fully serialised** "
         "(8 clients take 8x as long each, so throughput is flat).", "",
         "## Aggregate", "",
         "| clients | total throughput (queries/s) | median query slowdown vs 1 client |",
         "|---:|---:|---:|"]
    for c in sorted(cell["concurrency"].unique()):
        med = cell[cell["concurrency"] == c]["time_ms"].median() / \
            cell[cell["concurrency"] == 1]["time_ms"].median()
        L.append(f"| {c} | {tp.loc[c]:.2f} | {med:.2f}x |")
    L += ["",
          f"Median concurrency elasticity across queries: **{e.median():+.3f}** "
          f"(range {e.min():+.3f} to {e.max():+.3f}).", ""]
    if e.median() > 0.8:
        L.append("**Close to fully serialised.** At a median elasticity near 1, the machine is "
                 "saturated and extra clients buy almost no additional throughput — each query "
                 "simply waits. For a *predictor* this is the good case: a single contention "
                 "factor would capture most of it.")
    elif e.median() > 0.4:
        L.append("**Partial scaling.** Throughput rises with clients but sub-linearly, so "
                 "contention is real but not total. This is the awkward middle for a predictor: "
                 "the slowdown depends on the query, so a single global contention factor will "
                 "not do.")
    else:
        L.append("**Scales well.** Extra clients are largely absorbed, so single-client "
                 "measurements transfer to modest concurrency without correction.")
    L += ["", "## Per query", "",
          "| query | e_concurrency | t at 1 client (ms) | slowdown at 8 clients | distinct plans |",
          "|---|---:|---:|---:|---:|"]
    for r in per_q.itertuples():
        L.append(f"| {r.query_id} | {r.e_concurrency:+.3f} | {r.t_c1_ms:.0f} | "
                 + (f"{r.slowdown_c8:.2f}x" if not np.isnan(r.slowdown_c8) else "—")
                 + f" | {r.plans_across_concurrency} |")
    L += ["", "## Sanity check: does the plan change with concurrency?", "",
          f"**{drift} of {len(per_q)} queries** show more than one plan signature across "
          f"concurrency levels. Concurrency is not a planner input, so the expected answer is "
          f"zero."]
    if drift:
        L.append("")
        L.append("A non-zero count means something other than concurrency moved — most likely "
                 "autovacuum/autoanalyze altering statistics mid-sweep, since the sweep ran for "
                 "several minutes over a live database. Worth knowing: it bounds how stable "
                 "'the plan for query q' is even on fixed hardware and a fixed version, and it "
                 "is a confound for any longitudinal measurement in this project.")
    L += ["", "## Limitations", "",
          "- **One machine, and self-contention only** — N clients all run the *same* query. A "
          "real mixed workload contends differently, and that is not measured here.",
          "- 8 clients on 8 cores with `max_parallel_workers_per_gather=2` means the requested "
          "parallelism alone exceeds the core count, so some of the slowdown is scheduler "
          "queueing rather than resource contention.",
          "- 3 measured repetitions per cell, warmup discarded.", ""]
    (RESULTS_DIR / "tier2_concurrency.md").write_text("\n".join(L) + "\n")
    per_q.to_csv(RESULTS_DIR / "tier2_concurrency_per_query.csv", index=False)
    print(f"item 35: median e_concurrency {e.median():+.3f}, plan drift {drift}/{len(per_q)}")


def version_report():
    df = load_many("tpch_m1_pg*.csv")
    if df is None or df.empty:
        print("no version-matrix data; skipping item 36")
        return
    df["pg_major"] = df["pg_server_version"].astype(str).str.split(".").str[0]
    cell = (df.groupby(["query_id", "pg_major"])
            .agg(time_ms=("time_ms", "median"), cost=("cost", "median"),
                 signature=("plan_signature", "first"), dop=("dop", "median"),
                 node_count=("node_count", "median"))
            .reset_index())
    cell.to_csv(RESULTS_DIR / "tier2_versions.csv", index=False)

    versions = sorted(cell["pg_major"].unique(), key=int)
    sigs = cell.pivot(index="query_id", columns="pg_major", values="signature")
    times = cell.pivot(index="query_id", columns="pg_major", values="time_ms")
    changed = (sigs.nunique(axis=1) > 1)
    ref = "15" if "15" in versions else versions[0]

    L = ["# PostgreSQL version sensitivity (Tier 2, item 36)", "",
         f"The same 22 TPC-H queries on PostgreSQL {', '.join(versions)}, over "
         f"byte-identical data (moved by `pg_dump`, so only the engine differs). Tier 3 showed "
         f"the plan is hardware-dependent; this asks whether it is version-dependent too — "
         f"which matters because the entire EC2 dataset comes from a single version.", "",
         f"**{int(changed.sum())} of {len(sigs)} queries get a different plan on at least one "
         f"version.**", "",
         f"Runtime relative to PostgreSQL {ref} (median over queries):", "",
         "| version | median runtime ratio | queries with a different plan vs " + ref + " |",
         "|---|---:|---:|"]
    for v in versions:
        if v == ref:
            L.append(f"| {v} | 1.000 (reference) | — |")
            continue
        ratio = (times[v] / times[ref]).median()
        diff = int((sigs[v] != sigs[ref]).sum())
        L.append(f"| {v} | {ratio:.3f}x | {diff}/{len(sigs)} |")
    L += ["", "## Per query", "",
          "| query | " + " | ".join(f"t{v} (ms)" for v in versions) + " | distinct plans |",
          "|---|" + "|".join(["---:"] * len(versions)) + "|---:|"]
    for q in times.index:
        L.append(f"| {q} | " + " | ".join(
            f"{times.loc[q, v]:.0f}" if not pd.isna(times.loc[q, v]) else "—"
            for v in versions) + f" | {sigs.loc[q].nunique()} |")
    L += ["", "## Why this matters to the project", "",
          "The hardware signature is supposed to capture *the machine*. If a major version "
          "change alters plans and runtimes on identical hardware and identical data, then "
          "'production' is a pair — hardware **and** engine version — and a two-scalar hardware "
          "signature cannot express it. Any deployment comparing a dev box to a production "
          "server on a different PostgreSQL version is measuring both effects at once and "
          "attributing all of it to hardware.", "",
          "## Limitations", "",
          "- One machine, so this isolates the engine cleanly but says nothing about "
          "version x hardware interaction.",
          "- Default configuration per version, so some of the difference is changed *defaults* "
          "rather than changed planner logic. Separating those would need each version run "
          "with the others' settings explicitly pinned.",
          "- `pg_restore` moves data but statistics are rebuilt by `analyze` on each version, "
          "so small cost differences may be sampling noise in the statistics rather than "
          "planner changes.", ""]
    (RESULTS_DIR / "tier2_versions.md").write_text("\n".join(L) + "\n")
    print(f"item 36: {int(changed.sum())}/{len(sigs)} queries change plan across versions")


if __name__ == "__main__":
    concurrency_report()
    version_report()

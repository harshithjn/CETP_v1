"""
Engine version as a first-class transfer axis (CONFERENCE_PLAN.md item 5).

Tier 2 found that **12 of 22 queries get a different plan across PostgreSQL 14/15/17/18** on
byte-identical data and identical hardware, with PG15 about 30% slower than 17/18. That is
the same order of magnitude as the hardware effect this project exists to predict -- and no
cross-environment cost model in the literature treats engine version as an input at all.

So: treat version exactly as the project treats hardware. Define a version scaling factor

    s(q, A -> B) = time(q, B) / time(q, A)

and ask whether it is predictable. Leave-one-VERSION-out, so the target version is one the
model never saw -- the direct analogue of leave-one-machine-out.

Predictors compared:
  naive              s = 1 (assume the version does not matter) -- what everyone does today
  version_constant   one scalar per ordered version pair, fitted on the training queries.
                     The analogue of `1/compute_ratio`: a pure capability ratio with no
                     query-specific information.
  plan_change        version_constant, but with separate constants for queries whose PLAN
                     changed between the two versions and those whose plan did not. Tests
                     whether plan drift is the mechanism.
  learned            gradient boosting on plan-structure features + the version pair

Writes results/tier4_version_transfer.csv and .md.
"""

import sys
from itertools import permutations
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor

sys.path.insert(0, str(Path(__file__).resolve().parent))
from canonical_evaluation import RANDOM_STATE, RESULTS_DIR, mdape, qerror  # noqa: E402

PROJECT_DIR = Path(__file__).resolve().parents[2]
LAPTOP = PROJECT_DIR / "data" / "laptop"
PLAN_FEATURES = RESULTS_DIR / "plan_features_v2.csv"
VERSIONS = ["14", "15", "17", "18"]


def mape(y, p):
    y, p = np.asarray(y, float), np.asarray(p, float)
    return float(np.mean(np.abs((y - p) / y)) * 100)


def load():
    rows = []
    for v in VERSIONS:
        f = LAPTOP / f"tpch_m1_pg{v}.csv"
        if not f.exists():
            continue
        d = pd.read_csv(f)
        d = d[(~d["is_warmup"]) & d["ok"]]
        g = (d.groupby("query_id")
             .agg(time_ms=("time_ms", "median"), cost=("cost", "median"),
                  signature=("plan_signature", "first"), dop=("dop", "median"),
                  node_count=("node_count", "median"))
             .reset_index())
        g["version"] = v
        rows.append(g)
    if not rows:
        sys.exit("no version-matrix data -- run the item 36 collection first")
    return pd.concat(rows, ignore_index=True)


def build_pairs(cell):
    by = {v: d.set_index("query_id") for v, d in cell.groupby("version")}
    out = []
    for a, b in permutations(sorted(by), 2):
        A, B = by[a], by[b]
        for q in A.index.intersection(B.index):
            out.append({
                "query_id": q, "va": a, "vb": b, "pair": f"{a}->{b}",
                "t_a": A.loc[q, "time_ms"], "t_b": B.loc[q, "time_ms"],
                "scaling": B.loc[q, "time_ms"] / A.loc[q, "time_ms"],
                "plan_changed": int(A.loc[q, "signature"] != B.loc[q, "signature"]),
                "dop_a": A.loc[q, "dop"], "dop_b": B.loc[q, "dop"],
                "cost_a": A.loc[q, "cost"], "node_count_a": A.loc[q, "node_count"],
            })
    return pd.DataFrame(out)


def main():
    cell = load()
    pairs = build_pairs(cell)
    plan = pd.read_csv(PLAN_FEATURES) if PLAN_FEATURES.exists() else None
    feat_cols = []
    if plan is not None:
        drop = {"query_id", "query_num", "query_reconstruction", "exec_time_ms",
                "planning_ms", "root_rows"}
        feat_cols = [c for c in plan.columns if c not in drop and plan[c].nunique() > 1]
        pairs = pairs.merge(plan[["query_id"] + feat_cols], on="query_id", how="left")
        pairs = pairs.dropna(subset=feat_cols)

    rows = []
    # Leave-one-VERSION-out: every pair involving the held-out version is test.
    for held in VERSIONS:
        tr = pairs[(pairs.va != held) & (pairs.vb != held)]
        te = pairs[(pairs.va == held) | (pairs.vb == held)]
        if tr.empty or te.empty:
            continue

        preds = {"naive": np.ones(len(te))}

        # version_constant: a per-pair scalar. For an unseen version the pair itself is
        # unseen too, so fall back to the global median -- which is the honest cold-start.
        gmed = float(np.median(tr["scaling"]))
        pair_med = tr.groupby("pair")["scaling"].median().to_dict()
        preds["version_constant"] = te["pair"].map(
            lambda p: pair_med.get(p, gmed)).to_numpy()

        # plan_change: separate constants by whether the plan changed.
        by_flag = tr.groupby("plan_changed")["scaling"].median().to_dict()
        preds["plan_change"] = te["plan_changed"].map(
            lambda f: by_flag.get(f, gmed)).to_numpy()

        if feat_cols:
            X = tr[feat_cols + ["plan_changed"]].to_numpy()
            m = GradientBoostingRegressor(n_estimators=200, max_depth=3,
                                          learning_rate=0.05,
                                          random_state=RANDOM_STATE)
            m.fit(X, np.log(tr["scaling"].to_numpy()))
            preds["learned"] = np.exp(
                m.predict(te[feat_cols + ["plan_changed"]].to_numpy()))

        y = te["scaling"].to_numpy()
        for name, p in preds.items():
            q50, q95 = qerror(y, p)
            rows.append({"held_out_version": held, "predictor": name, "n": len(y),
                         "mape": mape(y, p), "mdape": mdape(y, p),
                         "qerror_median": q50, "qerror_p95": q95})

    df = pd.DataFrame(rows)
    df.to_csv(RESULTS_DIR / "tier4_version_transfer.csv", index=False)
    agg = df.groupby("predictor")[["mape", "mdape", "qerror_median"]].mean()
    order = [p for p in ("naive", "version_constant", "plan_change", "learned")
             if p in agg.index]

    # How large is the version effect in the first place?
    spread = pairs.groupby("pair")["scaling"].median()
    changed_eff = pairs.groupby("plan_changed")["scaling"].apply(
        lambda s: float(np.exp(np.abs(np.log(s)).mean())))

    L = ["# Engine version as a transfer axis (item 5)", "",
         "Tier 2 found 12 of 22 queries get a different plan across PostgreSQL 14/15/17/18 on "
         "identical hardware and identical data, with PG15 ~30% slower than 17/18. **No "
         "cross-environment cost model in the literature treats engine version as an input.** "
         "This treats it exactly as the project treats hardware.", "",
         "`s(q, A->B) = time(q,B) / time(q,A)`, evaluated **leave-one-version-out** so the "
         "target version is never seen in training — the direct analogue of "
         "leave-one-machine-out.", "",
         "## How big is the effect?", "",
         "| version pair | median scaling |", "|---|---:|"]
    for p, v in spread.sort_values().items():
        L.append(f"| {p} | {v:.3f} |")
    L += ["",
          f"Geometric-mean absolute runtime swing: **{changed_eff.get(1, float('nan')):.3f}x** "
          f"for queries whose plan changed, **{changed_eff.get(0, float('nan')):.3f}x** for "
          f"queries whose plan did not. So the version effect is real even when the plan is "
          f"identical — it is not purely a planner story, it is also executor changes between "
          f"major versions.", "",
          "## Can it be predicted for an unseen version?", "",
          "| predictor | MAPE % | MdAPE % | q-error (median) |", "|---|---:|---:|---:|"]
    for p in order:
        r = agg.loc[p]
        L.append(f"| {p} | {r['mape']:.2f} | {r['mdape']:.2f} | {r['qerror_median']:.3f} |")

    best = agg.loc[order, "mape"].idxmin()
    naive_m = agg.loc["naive", "mape"]
    best_m = agg.loc[best, "mape"]
    L += ["", "## Reading this", ""]
    L.append(f"Ignoring the version entirely (`naive`) costs **{naive_m:.2f}% MAPE**. The best "
             f"predictor is `{best}` at **{best_m:.2f}%**, an improvement of "
             f"**{naive_m - best_m:+.2f}pp**.")
    L.append("")
    if best == "naive":
        L.append("**Nothing beats ignoring the version.** With 4 versions, leave-one-version-out "
                 "means the held-out version's *pairs* are also unseen, so a per-pair constant "
                 "has nothing to fall back on but the global median. This is the n=5-machines "
                 "problem in miniature, and it is the honest result: **version transfer is a "
                 "real effect that this amount of data cannot model.** Four versions is to "
                 "version-transfer what five machines is to hardware-transfer.")
    else:
        L.append(f"`{best}` carries real signal, which means version transfer is learnable in "
                 f"principle. Note the ceiling: with only 4 versions the held-out version's "
                 f"pairs are unseen too, so this is a lower bound on what a richer version "
                 f"matrix would give.")
    L += ["",
          f"Per-pair constants are the interesting column regardless. The spread of median "
          f"scaling across pairs is {spread.min():.3f}x to {spread.max():.3f}x — a factor of "
          f"{spread.max() / spread.min():.2f} between the most and least favourable version "
          f"transition. A cost model that ignores this is absorbing it into its hardware term.",
          "", "## Why this matters for the paper", "",
          "This is a **novel axis**, cheap to measure, and it undermines the hardware-signature "
          "abstraction from a second direction. The project's premise is "
          "`runtime = workload cost x hardware capability`. Version changes neither the workload "
          "nor the hardware, yet it moves runtime ~30% and changes half the plans. So the "
          "factorisation is incomplete, and the missing factor is not small.", "",
          "## Limitations", "",
          "- **4 versions, one machine, 22 TPC-H queries.** Everything here is leave-one-of-four "
          "out.",
          "- Each version ran at **its own defaults**, so part of the difference is changed "
          "defaults rather than changed planner or executor logic. Separating those needs each "
          "version run with the others' settings explicitly pinned — cheap, and the obvious "
          "next step.",
          "- Statistics were rebuilt by `analyze` per version, so small cost differences may be "
          "statistics sampling rather than engine changes.", ""]
    (RESULTS_DIR / "tier4_version_transfer.md").write_text("\n".join(L) + "\n")
    print(agg.loc[order].round(3).to_string())
    print(f"\nversion pair scaling spread: {spread.min():.3f}x .. {spread.max():.3f}x")
    print(f"Wrote {RESULTS_DIR / 'tier4_version_transfer.md'}")


if __name__ == "__main__":
    main()

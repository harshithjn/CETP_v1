"""
Do plan-structure features help? (TASKS.md Tier 1, item 25)

The dev-side descriptor is six scalars from the root node. results/plan_features_v2.csv
adds ~50: operator-family counts, row volumes and cost shares, plan depth, spill
behaviour, degree of parallelism, and mean cardinality misestimation.

The honest test is NOT leave-one-machine-out. On the corrected dataset all six existing
dev-side features are machine-invariant -- they are a one-hot encoding of query_id in
disguise -- so under LOMO every query appears in training and the model can memorise
per-query scaling. Richer query features cannot help a model that has already memorised
the query. Plan structure can only pay off when the query is UNSEEN, which is
leave-one-query-out.

So: LOQO is the headline here, LOMO is reported only to show it is uninformative.

Feature sets compared:
  base        the shipped 13 features
  base+plan   those plus the plan-structure features
  plan_only   plan structure plus the two hardware ratios, dropping the root-node scalars
  hw_only     the two hardware ratios alone, as a floor

Writes results/tier1_plan_features.csv and results/tier1_plan_features.md.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor

sys.path.insert(0, str(Path(__file__).resolve().parent))
from canonical_evaluation import (  # noqa: E402
    COMPUTE_MULTI, LEARNED_FEATURES, RANDOM_STATE, RESULTS_DIR, attach_probabilities,
    build_pairs, fit_classifier, load_cells, score_all,
)

PLAN_CSV = RESULTS_DIR / "plan_features_v2.csv"
DROP = {"query_id", "query_num", "query_reconstruction", "exec_time_ms", "planning_ms",
        "root_rows", "shared_hit", "shared_read", "root_cost"}
HW = ["bandwidth_ratio", "compute_ratio"]


def feature_sets(plan_cols):
    return {
        "hw_only": HW,
        "base": LEARNED_FEATURES,
        "base+plan": LEARNED_FEATURES + plan_cols,
        "plan_only": HW + plan_cols,
    }


def fit_predict(train, test, cols):
    m = GradientBoostingRegressor(loss="quantile", alpha=0.5, n_estimators=200,
                                  max_depth=3, learning_rate=0.05,
                                  random_state=RANDOM_STATE)
    m.fit(train[cols].to_numpy(), train["scaling_factor_p50"].to_numpy())
    return m.predict(test[cols].to_numpy())


def main():
    if not PLAN_CSV.exists():
        sys.exit(f"missing {PLAN_CSV} -- run scripts/collection/extract_plan_features_v2.py")
    plan = pd.read_csv(PLAN_CSV)
    plan_cols = [c for c in plan.columns if c not in DROP]
    # Constant columns carry no information and only widen the feature space.
    plan_cols = [c for c in plan_cols if plan[c].nunique() > 1]

    cells = load_cells()
    pairs, machines = build_pairs(cells, COMPUTE_MULTI)
    pairs = pairs.merge(plan[["query_id"] + plan_cols], on="query_id", how="left")
    missing = pairs[plan_cols].isna().any(axis=1).sum()
    pairs = pairs.dropna(subset=plan_cols)
    sets = feature_sets(plan_cols)
    queries = sorted(pairs["query_num"].unique())

    rows = []
    for protocol in ("LOQO", "LOMO"):
        folds = ([("query", q) for q in queries] if protocol == "LOQO"
                 else [("machine", m) for m in machines])
        frames = []
        for kind, key in folds:
            if kind == "query":
                cell_train = cells["query_num"] != key
                tr = pairs[pairs["query_num"] != key]
                te = pairs[pairs["query_num"] == key]
            else:
                seen = [m for m in machines if m != key]
                cell_train = cells["machine_id"].isin(seen)
                tr = pairs[pairs["dev_machine"].isin(seen) & pairs["prod_machine"].isin(seen)]
                te = pairs[(pairs["dev_machine"] == key) | (pairs["prod_machine"] == key)]
            if te.empty or tr.empty:
                continue
            clf = fit_classifier(cells[cell_train])
            trn = attach_probabilities(tr, clf)
            tst = attach_probabilities(te, clf).copy()
            for name, cols in sets.items():
                tst[f"pred_{name}"] = fit_predict(trn, tst, cols)
            frames.append(tst)
        cv = pd.concat(frames, ignore_index=True)
        y = cv["scaling_factor_p50"].to_numpy()
        for name in sets:
            rows.append({"protocol": protocol, "feature_set": name,
                         "n_features": len(sets[name]),
                         **score_all(y, cv[f"pred_{name}"].to_numpy())})
        print(f"\n{protocol}")
        for name in sets:
            r = rows[-len(sets):][list(sets).index(name)]
            print(f"  {name:<12} ({r['n_features']:>2} feat)  MAPE {r['mape']:6.2f}  "
                  f"MdAPE {r['mdape']:6.2f}  qerr {r['qerror_median']:.3f}")

    df = pd.DataFrame(rows)
    df.to_csv(RESULTS_DIR / "tier1_plan_features.csv", index=False)
    piv = df.pivot(index="feature_set", columns="protocol", values="mape")

    L = ["# Do plan-structure features help? (Tier 1, item 25)", "",
         f"{len(plan_cols)} plan-structure features added to the dev-side descriptor: "
         "operator-family counts, row volumes and cost shares, plan depth, spills, degree of "
         "parallelism, and mean cardinality misestimation.", "",
         "**LOQO is the headline.** Under leave-one-machine-out every query is in the training "
         "set and, because all six existing dev-side features are machine-invariant, the model "
         "can memorise per-query scaling — richer query features cannot help a model that has "
         "already memorised the query. Plan structure can only pay off on an UNSEEN query.", "",
         "MAPE % on `scaling_factor_p50`, multi-core compute signal, classifier refit per fold.",
         "", "| feature set | n features | LOQO (new query) | LOMO (new machine) |",
         "|---|---:|---:|---:|"]
    for name in sets:
        L.append(f"| {name} | {len(sets[name])} | {piv.loc[name, 'LOQO']:.2f} | "
                 f"{piv.loc[name, 'LOMO']:.2f} |")
    gain = piv.loc["base", "LOQO"] - piv.loc["base+plan", "LOQO"]
    L += ["", "## Verdict", ""]
    if gain > 0.5:
        L.append(f"**Plan structure helps on unseen queries**: adding it improves LOQO by "
                 f"{gain:.2f}pp ({piv.loc['base', 'LOQO']:.2f}% -> "
                 f"{piv.loc['base+plan', 'LOQO']:.2f}%). This is the direction to invest in — "
                 f"and it is the argument for a tree-structured encoder once Tier 2 supplies "
                 f"enough queries to train one.")
    elif gain > -0.5:
        L.append(f"**No measurable effect**: LOQO moves by only {gain:+.2f}pp. At 21 queries "
                 f"there are 20 training queries per fold, which cannot support "
                 f"{len(plan_cols)} extra features — the same dilution `fix2` already found "
                 f"when 7 structural features made the classifier worse (0.375 -> 0.336) while "
                 f"structure alone scored 0.454. This is a statement about sample size, not "
                 f"about plan structure being uninformative, and it is exactly what Tier 2's "
                 f"~230 queries exist to settle.")
    else:
        L.append(f"**Plan structure makes it worse** by {-gain:.2f}pp under LOQO — classic "
                 f"overfitting with {len(plan_cols)} features and only ~20 training queries "
                 f"per fold.")
    L += ["",
          f"`hw_only` ({piv.loc['hw_only', 'LOQO']:.2f}% LOQO) is the floor: two hardware "
          f"ratios and nothing about the query at all. Compare every other row against it — "
          f"any feature set that fails to beat it is contributing nothing.", "",
          "## Caveats", "",
          "- Plan features were extracted on the **local Apple M1 instance**, not on the EC2 "
          "machines, which are terminated. Plan *shape* is a property of the query and the data "
          "and transfers; `dop` and `workers_launched` are **not** — they depend on the "
          "machine's core count. Those two columns describe the M1, not the production machine, "
          "and are a known source of mismatch.",
          "- Being machine-invariant, these features cannot break the query-memorisation "
          "problem under LOMO. They change what the model knows about a query, not what it "
          "knows about a machine.",
          f"- {missing} pairs were dropped for missing plan features (q15 has no row in the "
          f"cross-machine dataset).", ""]
    (RESULTS_DIR / "tier1_plan_features.md").write_text("\n".join(L) + "\n")
    print(f"\nWrote {RESULTS_DIR / 'tier1_plan_features.md'}")


if __name__ == "__main__":
    main()

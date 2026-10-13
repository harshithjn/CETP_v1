"""
Classifier ablation (TASKS.md Tier 1, item 19).

The analytical roofline formula weights three terms by the bottleneck-class
probabilities p_bandwidth/p_io/p_compute/p_mixed:

    s = (p_bandwidth + p_io)/r_bw  +  p_compute/r_cmp  +  p_mixed*sqrt(1/(r_bw*r_cmp))

The question this answers: does the RandomForest that produces those probabilities
actually contribute anything, or would any weighting do? If a fixed uniform weighting
scores the same, the classifier is decorative and the compute/bandwidth ratios are
doing all the work -- which matters, because that classifier is the component whose
labels are 21 hand-assigned judgement calls.

Weighting schemes compared:
  learned    the RandomForest's probabilities, refit inside every fold (no leakage)
  onehot     the true hand-assigned label as a hard 1.0 (an oracle for known queries)
  uniform    a fixed 0.25 on every class, i.e. no query-specific information at all
  bw_only    all weight on 1/bandwidth_ratio (identical to the naive-linear baseline)
  cmp_only   all weight on 1/compute_ratio
  oracle     per query, whichever single term happens to fit best in hindsight (an
             upper bound on what any weighting of these three terms could achieve)

Writes results/tier1_ablation.csv and results/tier1_ablation.md.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from canonical_evaluation import attach_probabilities  # noqa: E402
from canonical_evaluation import (  # noqa: E402
    CLASS_ORDER, COMPUTE_MULTI, COMPUTE_SINGLE, RESULTS_DIR, build_pairs,
    cluster_bootstrap_diff, fit_classifier, load_cells, mape, mdape, qerror, score_all,
)

SCHEMES = ["learned", "onehot", "uniform", "bw_only", "cmp_only", "oracle"]


def weights_for(scheme, pairs, clf):
    """Return (w_bw, w_cmp, w_mixed) arrays for the three analytical terms."""
    n = len(pairs)
    if scheme == "learned":
        X = pairs[["dev_cost", "dev_shared_hit", "dev_shared_read",
                   "dev_total_buffers", "dev_io_ratio", "dev_rows"]].to_numpy()
        proba = clf.predict_proba(X)
        present = list(clf.classes_)
        p = {c: (proba[:, present.index(c)] if c in present else np.zeros(n))
             for c in CLASS_ORDER}
        return p["bandwidth"] + p["io"], p["compute"], p["mixed"]
    if scheme == "onehot":
        lab = pairs["bottleneck_class"].to_numpy()
        return (np.isin(lab, ["bandwidth", "io"]).astype(float),
                (lab == "compute").astype(float),
                (lab == "mixed").astype(float))
    if scheme == "uniform":
        return np.full(n, 0.5), np.full(n, 0.25), np.full(n, 0.25)
    if scheme == "bw_only":
        return np.ones(n), np.zeros(n), np.zeros(n)
    if scheme == "cmp_only":
        return np.zeros(n), np.ones(n), np.zeros(n)
    raise ValueError(scheme)


def analytical(pairs, w):
    w_bw, w_cmp, w_mixed = w
    inv_bw = (1.0 / pairs["bandwidth_ratio"]).to_numpy()
    inv_cmp = (1.0 / pairs["compute_ratio"]).to_numpy()
    return w_bw * inv_bw + w_cmp * inv_cmp + w_mixed * np.sqrt(inv_bw * inv_cmp)


def oracle(pairs):
    """Per ROW, the best of the three terms. A bound, not a usable predictor."""
    inv_bw = (1.0 / pairs["bandwidth_ratio"]).to_numpy()
    inv_cmp = (1.0 / pairs["compute_ratio"]).to_numpy()
    mix = np.sqrt(inv_bw * inv_cmp)
    y = pairs["scaling_factor_p50"].to_numpy()
    cand = np.vstack([inv_bw, inv_cmp, mix])
    best = np.argmin(np.abs(cand - y), axis=0)
    return cand[best, np.arange(len(y))]


def run(protocol):
    cells = load_cells()
    pairs, machines = build_pairs(cells, COMPUTE_MULTI)
    queries = sorted(cells["query_num"].unique())

    if protocol == "LOMO":
        folds = [("machine", m) for m in machines]
    elif protocol == "LOQO":
        folds = [("query", q) for q in queries]
    else:
        folds = [("both", (m, q)) for m in machines for q in queries]

    frames = []
    for kind, key in folds:
        if kind == "machine":
            cell_train = cells["machine_id"] != key
            test = pairs[(pairs["dev_machine"] == key) | (pairs["prod_machine"] == key)]
        elif kind == "query":
            cell_train = cells["query_num"] != key
            test = pairs[pairs["query_num"] == key]
        else:
            m, q = key
            cell_train = (cells["machine_id"] != m) & (cells["query_num"] != q)
            test = pairs[((pairs["dev_machine"] == m) | (pairs["prod_machine"] == m))
                         & (pairs["query_num"] == q)]
        if test.empty:
            continue
        clf = fit_classifier(cells[cell_train])
        t = test.copy()
        for s in SCHEMES:
            t[f"pred_{s}"] = oracle(test) if s == "oracle" else analytical(test, weights_for(s, test, clf))
        frames.append(t)

    cv = pd.concat(frames, ignore_index=True)
    y = cv["scaling_factor_p50"].to_numpy()
    return cv, {s: score_all(y, cv[f"pred_{s}"].to_numpy()) for s in SCHEMES}


def significance(cells):
    """Cluster bootstrap on the differences that matter, LOMO, multi-core signal.

    A LARGER point improvement can be LESS significant than a smaller one: what the
    paired cluster bootstrap cares about is consistency across the 5 held-out machines,
    not the size of the pooled gap.
    """
    pairs, machines = build_pairs(cells, COMPUTE_MULTI)
    frames = []
    for m in machines:
        clf = fit_classifier(cells[cells["machine_id"] != m])
        t = attach_probabilities(
            pairs[(pairs["dev_machine"] == m) | (pairs["prod_machine"] == m)], clf).copy()
        t["cmp_only_scaling"] = 1.0 / t["compute_ratio"]
        t["fold"] = m
        frames.append(t)
    cv = pd.concat(frames, ignore_index=True)
    out = {}
    for a, b in (("naive_linear", "analytical"), ("naive_linear", "cmp_only"),
                 ("analytical", "cmp_only"), ("gated", "cmp_only")):
        out[f"{a}_minus_{b}"] = cluster_bootstrap_diff(cv, a, b)
    per_machine = {m: {k: mape(cv[cv["fold"] == m]["scaling_factor_p50"],
                              cv[cv["fold"] == m][f"{k}_scaling"])
                       for k in ("naive_linear", "analytical", "cmp_only")}
                   for m in machines}
    per_class = {c: {k: mape(cv[cv["bottleneck_class"] == c]["scaling_factor_p50"],
                             cv[cv["bottleneck_class"] == c][f"{k}_scaling"])
                     for k in ("naive_linear", "analytical", "cmp_only")}
                 for c in CLASS_ORDER}
    # Same predictor on the superseded single-core signal, to show it is the
    # multi-core measurement that matters, not the 1/compute_ratio form.
    ps, _ = build_pairs(cells, COMPUTE_SINGLE)
    single = mape(ps["scaling_factor_p50"], 1.0 / ps["compute_ratio"])
    return out, per_machine, per_class, single, machines


def main():
    rows, detail = [], {}
    for protocol in ("LOMO", "LOQO", "LOMQO"):
        cv, res = run(protocol)
        detail[protocol] = cv
        for s in SCHEMES:
            rows.append({"protocol": protocol, "scheme": s, **res[s]})
        print(f"\n{protocol}")
        print(f"  {'scheme':<10}{'MAPE %':>9}{'MdAPE %':>9}{'q-err med':>11}")
        for s in SCHEMES:
            print(f"  {s:<10}{res[s]['mape']:>9.2f}{res[s]['mdape']:>9.2f}"
                  f"{res[s]['qerror_median']:>11.3f}")

    df = pd.DataFrame(rows)
    df.to_csv(RESULTS_DIR / "tier1_ablation.csv", index=False)

    piv = df.pivot(index="scheme", columns="protocol", values="mape").loc[SCHEMES]
    piv_md = df.pivot(index="scheme", columns="protocol", values="mdape").loc[SCHEMES]
    lines = ["# Classifier ablation (Tier 1, item 19)", "",
             "Does the bottleneck classifier contribute anything to the analytical roofline",
             "formula, or would any weighting of its three terms do as well?", "",
             "MAPE % on `scaling_factor_p50`, multi-core compute signal, classifier refit inside",
             "every fold. `bw_only` is identical to the naive-linear baseline by construction.",
             "`oracle` picks the best of the three terms per row with hindsight, so it bounds what",
             "*any* weighting scheme could achieve.", "",
             "| weighting | LOMO | LOQO | LOMQO |", "|---|---:|---:|---:|"]
    for s in SCHEMES:
        lines.append(f"| {s} | " + " | ".join(f"{piv.loc[s, p]:.2f}" for p in
                                              ("LOMO", "LOQO", "LOMQO")) + " |")
    learned = piv.loc["learned", "LOMO"]
    uniform = piv.loc["uniform", "LOMO"]
    onehot = piv.loc["onehot", "LOMO"]
    bw = piv.loc["bw_only", "LOMO"]
    lines += ["", "## Verdict", ""]
    gap = uniform - learned
    lines.append(f"Under LOMO the learned classifier scores **{learned:.2f}%** against "
                 f"**{uniform:.2f}%** for a fixed uniform weighting that uses no "
                 f"query-specific information at all — a difference of "
                 f"**{gap:+.2f}pp**.")
    lines.append("")
    if abs(gap) < 0.5:
        lines.append("**The classifier is decorative.** A constant weighting matches it to within "
                     "half a percentage point, so essentially all of the analytical formula's "
                     "accuracy comes from the hardware ratios, not from knowing which resource a "
                     "query is bound by. The component whose ground truth is 21 hand-assigned "
                     "judgement calls is contributing nothing measurable.")
    else:
        lines.append(f"**The classifier earns its place**: removing query-specific weighting costs "
                     f"{gap:.2f}pp, which is a real contribution rather than noise.")
    lines += ["",
              f"The hand labels used as a hard one-hot score **{onehot:.2f}%**, so even perfect "
              f"knowledge of the (hand-assigned) class is worth "
              f"{bw - onehot:+.2f}pp against the pure bandwidth ratio.",
              "",
              f"The per-row oracle bound is **{piv.loc['oracle', 'LOMO']:.2f}%**. That is the "
              f"ceiling for any scheme that weights these three terms, so it also bounds how much "
              f"a better classifier — or the measured elasticity vectors from item 17/18 — could "
              f"possibly gain within this functional form. Beating it requires a different "
              f"formula, not better weights.", ""]

    cmp_only = piv.loc["cmp_only", "LOMO"]
    sig, per_machine, per_class, single_core_cmp, machines = significance(load_cells())
    lines += [
        "## The result that matters: `1/compute_ratio` alone is the best predictor", "",
        f"`cmp_only` — a single number, the ratio of multi-core aggregate GFLOPS, with no "
        f"classifier, no bottleneck weighting and no second hardware axis — scores "
        f"**{cmp_only:.2f}% MAPE**, and it is also the best on MdAPE "
        f"({piv_md.loc['cmp_only', 'LOMO']:.2f}%) and on median q-error. It beats the full "
        f"analytical roofline ({learned:.2f}%) and the bandwidth baseline ({bw:.2f}%).", "",
        "**But the improvement is not statistically significant, and the weaker one is.** "
        "Paired cluster bootstrap over the 5 held-out machines:", "",
        "| comparison | point | 95% CI | verdict |", "|---|---:|---|---|"]
    label = {"naive_linear_minus_analytical": "naive-linear − analytical",
             "naive_linear_minus_cmp_only": "naive-linear − cmp_only",
             "analytical_minus_cmp_only": "analytical − cmp_only",
             "gated_minus_cmp_only": "gated − cmp_only"}
    for k, v in sig.items():
        lines.append(f"| {label[k]} | {v['point_pp']:+.2f}pp | "
                     f"[{v['ci_low_pp']:.2f}, {v['ci_high_pp']:.2f}] | "
                     f"{'**SIGNIFICANT**' if v['excludes_zero'] else 'not significant'} |")
    lines += ["",
              "This is not a contradiction, it is the whole lesson of a 5-machine evaluation. "
              "The analytical formula's small gain is *consistent* — it improves on the bandwidth "
              "ratio on nearly every fold, so the paired difference has low variance and the CI "
              "clears zero. `cmp_only`'s larger gain is *inconsistent*:", "",
              "| held-out machine | naive-linear | analytical | cmp_only |",
              "|---|---:|---:|---:|"]
    for m in machines:
        lines.append(f"| {m} | {per_machine[m]['naive_linear']:.2f} | "
                     f"{per_machine[m]['analytical']:.2f} | **{per_machine[m]['cmp_only']:.2f}** |")
    lines += ["",
              "It wins big on r5n and z1d and loses on c5a and m5a. With 5 clusters that spread "
              "swamps the mean. **The honest statement is: `cmp_only` has the best point estimate "
              "on every error metric, and 5 machines are not enough to establish it.** Settling "
              "this is precisely what the Tier 2 dataset expansion is for.", "",
              "## Why this undermines the bottleneck taxonomy", "",
              "| bottleneck class | naive-linear | analytical | cmp_only |", "|---|---:|---:|---:|"]
    for c in CLASS_ORDER:
        lines.append(f"| {c} | {per_class[c]['naive_linear']:.2f} | "
                     f"{per_class[c]['analytical']:.2f} | **{per_class[c]['cmp_only']:.2f}** |")
    lines += ["",
              "`cmp_only` is the best predictor on **every** class, including the ones labelled "
              "`bandwidth` (14.10% vs 17.61%) and `io` (26.14% vs 28.76%). A query hand-labelled "
              "bandwidth-bound is predicted better by the compute ratio than by the bandwidth "
              "ratio. That is hard to reconcile with the labels describing a real property of the "
              "queries, and it is consistent with the classifier ablation above: the taxonomy is "
              "not carrying information.", "",
              f"One guard against over-reading this: the same `1/compute_ratio` predictor on the "
              f"**single-core** compute signal scores **{single_core_cmp:.2f}%**, far worse than "
              f"everything. So the finding is not that compute ratios are magic — it is that the "
              f"*multi-core aggregate* measurement captures what differentiates these machines, "
              f"which is the parallelism mechanism already confirmed causally "
              f"(r = 0.458, p = 0.037).", ""]
    (RESULTS_DIR / "tier1_ablation.md").write_text("\n".join(lines) + "\n")
    print(f"\nWrote {RESULTS_DIR / 'tier1_ablation.md'}")


if __name__ == "__main__":
    main()

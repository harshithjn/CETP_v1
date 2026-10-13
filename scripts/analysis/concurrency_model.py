"""
Concurrency: the largest unmodelled term, fitted and validated.

Every prediction elsewhere in this project is single-client. Production is not. Measured
concurrency elasticity on this workload ranges +0.286 to +0.984 across queries, i.e. up to
an 8.57x slowdown at 8 clients -- several times larger than the whole cross-environment
prediction error the project spends its effort on.

THE MODEL. For client count N, runtime is assumed to follow a power law

    T(N) = T(1) * N^e

so e = 0 means the machine absorbs the extra load for free and e = 1 means fully serialised
(throughput flat). `e` is fitted per query by least squares on log N vs log T.

THE QUESTION THIS ANSWERS. A product needs e for a query it has never measured under load.
Three candidates, each honestly cross-validated leave-one-query-out:

    global      one e for every query (the median of the training queries)
    plan        e predicted from plan features available WITHOUT running under load
    oracle      the query's own fitted e -- not attainable, included as the ceiling

If `plan` does not beat `global`, then concurrency cannot be predicted from a plan and the
honest product behaviour is to require the user to supply their load factor. That is a
legitimate answer and it must be reported rather than hidden.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR))

LAPTOP = PROJECT_DIR / "data" / "laptop"
RESULTS = PROJECT_DIR / "results"
CLIENTS = [1, 2, 4, 8]
DISCARD_WARMUP_REPS = 1


def load():
    """Warm median runtime per (query, client count)."""
    rows = []
    for n in CLIENTS:
        f = LAPTOP / f"tpch_m1_c{n}.csv"
        if not f.exists():
            sys.exit(f"missing {f}")
        d = pd.read_csv(f)
        d = d[d["ok"] == True]                                       # noqa: E712
        for q, g in d.groupby("query_id"):
            # With N clients the same (query, rep) appears once per client; take the
            # per-client warm median, then the median across clients -- that is the
            # latency ONE client experiences, which is what an SLA is written against.
            per_client = []
            for _c, cg in g.groupby("client"):
                t = cg.sort_values("rep")["time_ms"].to_numpy()
                t = t[DISCARD_WARMUP_REPS:] if len(t) > DISCARD_WARMUP_REPS else t
                if len(t):
                    per_client.append(float(np.median(t)))
            if per_client:
                rows.append({"query_id": q, "clients": n,
                             "time_ms": float(np.median(per_client)),
                             "n_clients_seen": len(per_client)})
    return pd.DataFrame(rows)


def fit_elasticity(g):
    """Least-squares slope of log T against log N."""
    x = np.log(g["clients"].to_numpy(float))
    y = np.log(g["time_ms"].to_numpy(float))
    if len(x) < 2:
        return np.nan, np.nan
    slope, intercept = np.polyfit(x, y, 1)
    pred = slope * x + intercept
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan
    return float(slope), r2


PLAN_FEATURES = ["cost", "rows", "shared_hit", "shared_read", "io_ratio", "t1_ms"]


def plan_features(df):
    """Features knowable from a single-client run, i.e. without ever running under load."""
    f = LAPTOP / "tpch_m1_c1.csv"
    d = pd.read_csv(f)
    d = d[d["ok"] == True]                                           # noqa: E712
    agg = d.groupby("query_id").agg(
        cost=("cost", "median"), rows=("rows", "median"),
        shared_hit=("shared_hit", "median"), shared_read=("shared_read", "median"),
        dop=("dop", "median") if "dop" in d else ("cost", "median"),
    )
    agg["io_ratio"] = agg["shared_read"] / (agg["shared_hit"] + agg["shared_read"] + 1)
    t1 = df[df["clients"] == 1].set_index("query_id")["time_ms"]
    agg["t1_ms"] = t1
    return agg


def main():
    df = load()
    print(f"{df['query_id'].nunique()} queries x {sorted(df['clients'].unique())} clients")

    fits = {}
    for q, g in df.groupby("query_id"):
        e, r2 = fit_elasticity(g.sort_values("clients"))
        fits[q] = {"e": e, "r2": r2}
    el = pd.DataFrame(fits).T
    el.index.name = "query_id"
    print(f"\nelasticity: median {el['e'].median():.3f}, "
          f"range {el['e'].min():.3f} to {el['e'].max():.3f}")
    print(f"power-law fit quality: median R^2 {el['r2'].median():.3f}, "
          f"min {el['r2'].min():.3f}")
    bad = el[el["r2"] < 0.9]
    if len(bad):
        print(f"  {len(bad)} queries fit the power law poorly (R^2 < 0.9): "
              f"{sorted(bad.index)}")

    feats = plan_features(df)
    data = feats.join(el, how="inner").dropna(subset=["e"])
    print(f"\nqueries usable for prediction: {len(data)}")

    # ---- leave-one-query-out comparison of the three candidates -------------------
    from sklearn.ensemble import RandomForestRegressor

    qs = sorted(data.index)
    recs = []
    for q in qs:
        train, test = data.drop(index=q), data.loc[[q]]
        g_pred = float(train["e"].median())
        rf = RandomForestRegressor(n_estimators=300, random_state=42)
        rf.fit(train[PLAN_FEATURES].to_numpy(), train["e"].to_numpy())
        p_pred = float(rf.predict(test[PLAN_FEATURES].to_numpy())[0])
        recs.append({"query_id": q, "actual_e": float(test["e"].iloc[0]),
                     "global": g_pred, "plan": p_pred,
                     "oracle": float(test["e"].iloc[0])})
    cv = pd.DataFrame(recs)

    print(f"\nleave-one-query-out prediction of elasticity e:")
    print(f"{'method':8s} {'MAE(e)':>8s} {'RMSE(e)':>8s}")
    e_err = {}
    for m in ("global", "plan"):
        mae = float(np.mean(np.abs(cv[m] - cv["actual_e"])))
        rmse = float(np.sqrt(np.mean((cv[m] - cv["actual_e"]) ** 2)))
        e_err[m] = {"mae": mae, "rmse": rmse}
        print(f"{m:8s} {mae:8.4f} {rmse:8.4f}")

    # ---- what the elasticity error costs in RUNTIME terms -------------------------
    # Predict T(N) from T(1) and the predicted e, score against the measured T(N).
    print(f"\nruntime prediction under load, from T(1) and a predicted e:")
    print(f"{'method':8s} " + " ".join(f"N={n:<9d}" for n in CLIENTS[1:]))
    runtime_err = {}
    for m in ("global", "plan", "oracle"):
        row = []
        for n in CLIENTS[1:]:
            errs = []
            for _, r in cv.iterrows():
                t1 = float(data.loc[r["query_id"], "t1_ms"])
                actual = df[(df["query_id"] == r["query_id"]) & (df["clients"] == n)]
                if actual.empty:
                    continue
                pred = t1 * (n ** r[m])
                a = float(actual["time_ms"].iloc[0])
                errs.append(abs(pred - a) / a * 100)
            row.append(float(np.mean(errs)))
        runtime_err[m] = dict(zip(map(str, CLIENTS[1:]), row))
        print(f"{m:8s} " + " ".join(f"{v:9.2f}%" for v in row))
    # and the baseline of ignoring concurrency entirely
    row = []
    for n in CLIENTS[1:]:
        errs = []
        for q in qs:
            t1 = float(data.loc[q, "t1_ms"])
            a = df[(df["query_id"] == q) & (df["clients"] == n)]
            if a.empty:
                continue
            errs.append(abs(t1 - float(a["time_ms"].iloc[0])) / float(a["time_ms"].iloc[0]) * 100)
        row.append(float(np.mean(errs)))
    runtime_err["ignore_concurrency"] = dict(zip(map(str, CLIENTS[1:]), row))
    print(f"{'ignore':8s} " + " ".join(f"{v:9.2f}%" for v in row))

    verdict = ("plan features beat a global constant"
               if e_err["plan"]["mae"] < e_err["global"]["mae"]
               else "plan features do NOT beat a global constant -- concurrency is not "
                    "predictable from a single-client plan, and the load factor must be "
                    "supplied by the caller")
    print(f"\nVERDICT: {verdict}")

    payload = {
        "machine": "m1-host (Apple M1, 8 cores)",
        "clients": CLIENTS, "n_queries": int(len(data)),
        "warmup_reps_discarded": DISCARD_WARMUP_REPS,
        "elasticity": {"median": float(el["e"].median()),
                       "min": float(el["e"].min()), "max": float(el["e"].max()),
                       "median_r2": float(el["r2"].median()),
                       "per_query": {q: round(float(v["e"]), 4) for q, v in fits.items()}},
        "elasticity_prediction_loqo": e_err,
        "runtime_error_under_load_pct": runtime_err,
        "verdict": verdict,
        "caveat": "one machine (8 cores). Elasticity is a property of the machine's core "
                  "count and the query mix as much as of the query, so these values do not "
                  "transfer to a machine with a different core count without re-measurement.",
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "concurrency_model_metrics.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n")
    cv.to_csv(RESULTS / "concurrency_model_loqo.csv", index=False)
    print(f"\nwrote {RESULTS / 'concurrency_model_metrics.json'}")


if __name__ == "__main__":
    main()

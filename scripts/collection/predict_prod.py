"""
End-to-end: predict a query's production runtime from the developer's machine.

This is the four-move flow over live connections, and it is the script that makes move 3
real -- production's plan is fetched and FORCED on development, so plan divergence cannot
contribute:

  1. production: EXPLAIN (no ANALYZE)          -- plans only, executes nothing
  2. development: force that plan with pg_hint_plan, EXPLAIN (ANALYZE)
  3. verify the forced plan's signature matches production's, or say it did not
  4. price each plan node by the two machines' operator rates and report

Only step 1 touches production, and it does not run the query. With `--calibrate k` the
script will additionally RUN k queries on production to fit a correction -- that does
execute on production, so it is opt-in and off by default.

  # register both machines first (SQL only, works on RDS)
  python3 scripts/collection/operator_probe.py --machine-id dev-laptop --db tpch
  python3 scripts/collection/operator_probe.py --machine-id prod-db --db tpch \
      --host prod.example.com --user readonly

  python3 scripts/collection/predict_prod.py \
      --query-file q.sql --dev dev-laptop --prod prod-db \
      --prod-host prod.example.com --prod-db tpch --prod-user readonly \
      --dev-db tpch

REQUIREMENTS. pg_hint_plan on the DEVELOPMENT machine only (production is never asked to
force anything). Without it, pass --no-force and the prediction is reported with
plan_forced=False and the plan-divergence warning live.
"""

import argparse
import json
import statistics
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR))
sys.path.insert(0, str(PROJECT_DIR / "scripts" / "collection"))

from cetp import cross_env                                          # noqa: E402
from cetp.calibration_store import CalibrationStore                 # noqa: E402
from cetp.operator_model import fit_calibration, select_calibration_queries  # noqa: E402
from cetp.telemetry import Telemetry                                # noqa: E402

try:
    import psycopg2
except ImportError:
    sys.exit("psycopg2 is required: pip install psycopg2-binary")

from plan_to_hints import force_and_verify, plan_signature, run_explain  # noqa: E402

DEFAULT_REGISTRY = PROJECT_DIR / "models" / "operator_signature.json"


def connect(host, port, db, user, password):
    kw = {"dbname": db, "port": port}
    if host:
        kw["host"] = host
    if user:
        kw["user"] = user
    if password:
        kw["password"] = password
    return psycopg2.connect(**kw)


def explain_only(cur, sql):
    """Production side: plan the query without executing it."""
    cur.execute(f"EXPLAIN (FORMAT JSON) {sql}")
    return cur.fetchone()[0][0]


def dev_measure(cur, sql, target_plan, reps, force=True):
    """
    Run the query on development `reps` times, forcing production's plan if asked.

    Returns (plan_json, warm_median_ms, forced_flag). The first repetition is discarded as
    cold-cache warmup -- at 20 repetitions the cold run lands exactly where p95 sits, which
    is how every percentile in the original dataset came to be contaminated.
    """
    times, plan, forced = [], None, None
    for i in range(reps):
        if force and target_plan is not None:
            res, _hints, ok = force_and_verify(cur, sql, target_plan)
            if res is None:
                res, ok = run_explain(cur, sql), False
        else:
            res, ok = run_explain(cur, sql), None
        times.append(res["Execution Time"])
        if i == 0:
            plan, forced = res, ok
    warm = sorted(times[1:]) if len(times) > 1 else times
    return plan, float(statistics.median(warm)), forced


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--query-file", required=True)
    ap.add_argument("--dev", required=True, help="dev machine id in the registry")
    ap.add_argument("--prod", required=True, help="prod machine id in the registry")
    ap.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    ap.add_argument("--dev-db", default="tpch")
    ap.add_argument("--dev-host"), ap.add_argument("--dev-port", type=int, default=5432)
    ap.add_argument("--dev-user"), ap.add_argument("--dev-password")
    ap.add_argument("--prod-db", default="tpch")
    ap.add_argument("--prod-host"), ap.add_argument("--prod-port", type=int, default=5432)
    ap.add_argument("--prod-user"), ap.add_argument("--prod-password")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--no-force", action="store_true",
                    help="do not force production's plan on dev (needs no pg_hint_plan)")
    ap.add_argument("--no-prod-plan", action="store_true",
                    help="do not contact production at all; predicts DEV's plan")
    ap.add_argument("--calibration", type=float, default=1.0,
                    help="override; by default the stored correction for this pair is used")
    ap.add_argument("--no-store", action="store_true",
                    help="ignore the calibration store and do not log telemetry")
    ap.add_argument("--actual-ms", type=float,
                    help="the measured production runtime, if you already know it. "
                         "Resolves the telemetry row and feeds the calibration store, "
                         "which is how the correction for this pair improves.")
    ap.add_argument("--sla-ms", type=float)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    sql = Path(args.query_file).read_text().strip().rstrip(";")

    try:
        dev_rates, dev_rec = cross_env.load_rates(args.registry, args.dev)
        prod_rates, prod_rec = cross_env.load_rates(args.registry, args.prod)
        cross_env.assert_comparable(dev_rec, prod_rec)
    except (KeyError, ValueError) as exc:
        sys.exit(f"signature error: {exc}")

    prod_plan = None
    if not args.no_prod_plan:
        with connect(args.prod_host, args.prod_port, args.prod_db,
                     args.prod_user, args.prod_password) as pc:
            with pc.cursor() as cur:
                prod_plan = explain_only(cur, sql)
        print(f"production planned the query (not executed); "
              f"plan signature {plan_signature(prod_plan['Plan'])}", file=sys.stderr)

    with connect(args.dev_host, args.dev_port, args.dev_db,
                 args.dev_user, args.dev_password) as dc:
        with dc.cursor() as cur:
            dev_plan, dev_ms, forced = dev_measure(
                cur, sql, prod_plan, args.reps, force=not args.no_force)

    # Use the stored correction for this pair unless the caller overrode it. This is the
    # mechanism by which accuracy improves with use: each resolved observation refits it.
    store = None if args.no_store else CalibrationStore()
    calibration, calib_meta = args.calibration, None
    if store is not None and args.calibration == 1.0:
        calib_meta = store.calibration(args.dev, args.prod)
        calibration = calib_meta["factor"]

    try:
        out = cross_env.predict(prod_plan, dev_plan, dev_ms, dev_rates, prod_rates,
                                plan_forced=forced, calibration=calibration,
                                dev_speedup=dev_rec.get("parallel_speedup") or 1.0,
                                prod_speedup=prod_rec.get("parallel_speedup") or 1.0,
                                dev_rec=dev_rec, prod_rec=prod_rec)
    except cross_env.CrossArchitectureUncalibrated as exc:
        # An expected outcome, not a crash. Say what to do about it.
        print(f"\nREFUSED: {exc}\n", file=sys.stderr)
        print(f"To calibrate: run this query on {args.prod}, then re-run with "
              f"--actual-ms <measured ms>. Repeat for 3-5 queries and the refusal lifts.",
              file=sys.stderr)
        return 4
    out["query_file"] = args.query_file
    out["calibration_source"] = calib_meta

    query_id = Path(args.query_file).stem
    if store is not None:
        tel = Telemetry()
        tel.record_prediction(
            query_id, args.dev, args.prod,
            predicted_ms=out["predicted_prod_ms"], dev_ms=dev_ms,
            scaling=out["scaling"], trust=out["trust"],
            calibration=calibration,
            cross_architecture=out.get("cross_architecture"))
        if args.actual_ms:
            tel.resolve(query_id, args.actual_ms, dev=args.dev, prod=args.prod)
            store.record(args.dev, args.prod, query_id,
                         out["scaling"] / max(calibration, 1e-12), args.actual_ms / dev_ms,
                         dev_arch=dev_rec.get("client_platform"),
                         prod_arch=prod_rec.get("client_platform")).save()
            out["calibration_after"] = store.calibration(args.dev, args.prod)

    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True, default=float))
        return 0

    print(f"\n{args.dev} -> {args.prod}  ({Path(args.query_file).name})")
    print(f"  dev runtime (warm median of {args.reps}): {dev_ms:,.1f} ms")
    print(f"  plan source:        {out['plan_source']}")
    print(f"  parallel speedup:   dev {out['dev_speedup']:.2f}x -> "
          f"prod {out['prod_speedup']:.2f}x")
    print(f"  scaling factor:     {out['scaling']:.4f}")
    print(f"  PREDICTED PROD:     {out['predicted_prod_ms']:,.1f} ms")
    print(f"  explained:          {out['explained_frac']:.0%} of dev wall-time priced")
    for ax, w in sorted(out["weights_by_parallelism"].items(), key=lambda kv: -kv[1]):
        if w <= 0:
            continue
        r = out["per_axis_ratio"].get(ax)
        print(f"    {ax:18s} weight {w:5.1%}   "
              f"ratio {f'{r:.3f}' if r is not None else 'unpriced'}")
    if args.sla_ms:
        print(f"  SLA {args.sla_ms:,.0f} ms -> "
              f"{'PASS' if out['predicted_prod_ms'] <= args.sla_ms else 'BREACH'}")
    if calib_meta is not None:
        prov = " (PROVISIONAL)" if calib_meta["provisional"] else ""
        print(f"  calibration:        x{calib_meta['factor']:.4f} from "
              f"{calib_meta['n']} observation(s){prov}")
    if out.get("calibration_after"):
        a = out["calibration_after"]
        print(f"  recorded the actual; correction for this pair is now "
              f"x{a['factor']:.4f} over {a['n']} observation(s)")
    print(f"  trust:              {out['trust']}")
    for w in out["warnings"]:
        print(f"  ! {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

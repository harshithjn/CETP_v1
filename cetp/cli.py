"""
cetp command line (Tier 4).

    cetp predict   --plan p.json --dev c7i --prod c5a [--sla-ms N]
    cetp regress   --before b.json --after a.json --dev c7i --prod c5a
    cetp redact    plan.json [--level full]
    cetp signature list | probe --machine-id ID
    cetp selftest

`regress` is the recommended mode. Absolute prediction carries ~21.6% MAPE on an unseen
machine, which is not enough to gate an SLA (54.5% of BLOCK verdicts were false alarms).
Regression detection divides the hardware term out, so it is defensible at this accuracy --
see cetp/regress.py for the arithmetic.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import predict as _predict
from . import redact as _redact
from . import regress as _regress
from . import signature as _sig

PROJECT_DIR = Path(__file__).resolve().parents[1]
_DEFAULT_OP_REGISTRY = PROJECT_DIR / "models" / "operator_signature.json"


def _load_json(path):
    """Read a plan from a file, a .gz, or stdin."""
    if path == "-":
        return json.load(sys.stdin)
    pth = Path(path)
    if pth.suffix == ".gz":
        import gzip
        with gzip.open(pth, "rt") as fh:
            return json.load(fh)
    return json.loads(pth.read_text())


def _load(path):
    raw = sys.stdin.read() if path == "-" else Path(path).read_text()
    return json.loads(raw)


def _resolve(machine_id, explicit_bandwidth=None, explicit_compute=None):
    if explicit_bandwidth is not None and explicit_compute is not None:
        return {"machine_id": machine_id or "custom", "bandwidth": explicit_bandwidth,
                "compute": explicit_compute, "source": "legacy_frozen",
                "comparable_with": "legacy_frozen", "nproc": None,
                "dop_mismatch": False}
    s = _sig.get_signature(machine_id)
    if not s:
        known = _sig.list_machines()
        sys.exit(f"unknown machine {machine_id!r}.\n"
                 f"  registry: {', '.join(known['registry']) or '(empty)'}\n"
                 f"  legacy:   {', '.join(known['legacy'])}\n"
                 f"Probe a new machine with: cetp signature probe --machine-id ID")
    return s


def cmd_predict(args):
    plan = _load(args.plan)
    dev = _resolve(args.dev, args.dev_bandwidth, args.dev_compute)
    prod = _resolve(args.prod, args.prod_bandwidth, args.prod_compute)
    r = _predict.predict(plan, dev, prod, predictor=args.predictor)
    if args.json:
        print(json.dumps(r, indent=2, default=str))
        return 0
    print(f"CETP prediction ({args.dev} -> {args.prod}, predictor={args.predictor})")
    print(f"  dev runtime:       {r['dev_time_ms']:.1f} ms")
    print(f"  scaling factor:    {r['scaling_factor']:.4f}  "
          f"(bandwidth_ratio={r['ratios']['bandwidth_ratio']:.3f} "
          f"compute_ratio={r['ratios']['compute_ratio']:.3f})")
    print(f"  predicted prod:    {r['predicted_ms']:.1f} ms")
    for lv, v in sorted(r["bounds_ms"].items()):
        print(f"    {float(lv):.0%} upper bound: {v:.1f} ms")
    print(f"  confidence:        {r['confidence']:.2f}"
          + ("  (LOW)" if r["low_confidence"] else ""))
    if args.sla_ms:
        ub = r["bounds_ms"].get("0.95")
        state = ("PASS" if ub and ub <= args.sla_ms
                 else "BLOCK" if r["predicted_ms"] > args.sla_ms else "WARN")
        print(f"  SLA {args.sla_ms:.0f} ms -> {state}")
        if state == "WARN":
            print("    point estimate clears the SLA but the 95% bound does not; "
                  "run it for real")
    print("\n  " + r["accuracy_note"])
    for w in r["warnings"]:
        print(f"  ! {w}")
    return 0


def cmd_regress(args):
    before, after = _load(args.before), _load(args.after)
    dev = _resolve(args.dev, args.dev_bandwidth, args.dev_compute)
    prod = _resolve(args.prod, args.prod_bandwidth, args.prod_compute)
    r = _regress.detect(before, after, dev, prod, predictor=args.predictor,
                        threshold=args.threshold)
    if args.json:
        print(json.dumps(r, indent=2, default=str))
    else:
        print(_regress.format_report(r, args.query_id))
        for w in r["warnings"]:
            print(f"  ! {w}")
    # Exit non-zero on a regression so CI fails without extra glue.
    return 1 if (args.fail_on_regression and r["verdict"] == "REGRESSION") else 0


def cmd_redact(args):
    return _redact.main([args.plan, "-o", args.out, "--level", args.level]
                        + (["--check"] if args.check else []))


def cmd_signature(args):
    if args.action == "list":
        reg = _sig.load_registry()
        print("legacy (frozen, bandwidth probe lost and unreproducible):")
        for m in sorted(_sig.LEGACY):
            s = _sig.LEGACY[m]
            flag = "  [DOP mismatch]" if s["nproc"] > _sig.PG_DEFAULT_DOP else ""
            print(f"  {m:<10} bandwidth={s['bandwidth']:<6} compute={s['compute']:<9.3f}"
                  f" nproc={s['nproc']}{flag}")
        machines = reg.get("machines", {})
        print(f"\nregistry ({len(machines)} probed with hw_probe_suite v2):")
        for m in sorted(machines):
            s = _sig.registry_signature(m)
            print(f"  {m:<14} bandwidth={s['bandwidth']:.2f} compute={s['compute']:.2f} "
                  f"(at DOP {s['achievable_dop']}) latency={s['mem_latency_ns']:.0f}ns"
                  + ("" if s["validated"] else "  [VALIDATION FAILED]"))
        if not machines:
            print("  (empty) -- probe this machine with: "
                  "cetp signature probe --machine-id <id>")
        return 0
    if args.action == "probe":
        if not args.machine_id:
            sys.exit("probe needs --machine-id")
        return subprocess.call(
            [sys.executable, str(PROJECT_DIR / "scripts/collection/hw_signature.py"),
             "--machine-id", args.machine_id, "--db", args.db, "--port", str(args.port)])
    return 0


def cmd_predict_prod(args):
    """The four-move flow: operator rates, per-operator scaling, production's plan, few-shot.

    Takes production's EXPLAIN (no ANALYZE) and development's EXPLAIN ANALYZE of the SAME
    plan, and reports the production runtime. The caller is responsible for forcing
    production's plan on dev (`--plan-forced`); `scripts/collection/predict_prod.py` does
    that over a live connection.
    """
    import json as _json
    from . import cross_env

    reg = args.registry or str(_DEFAULT_OP_REGISTRY)
    try:
        dev_rates, dev_rec = cross_env.load_rates(reg, args.dev)
        prod_rates, prod_rec = cross_env.load_rates(reg, args.prod)
        cross_env.assert_comparable(dev_rec, prod_rec)
    except (KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    dev_plan = _load_json(args.dev_plan)
    prod_plan = _load_json(args.prod_plan) if args.prod_plan else None

    dev_ms = args.dev_runtime_ms
    if dev_ms is None:
        root = dev_plan[0] if isinstance(dev_plan, list) else dev_plan
        dev_ms = root.get("Execution Time")
        if dev_ms is None:
            print("error: dev plan has no Execution Time; pass --dev-runtime-ms or use "
                  "EXPLAIN (ANALYZE)", file=sys.stderr)
            return 2

    plan_forced = None
    if args.plan_forced == "yes":
        plan_forced = True
    elif args.plan_forced == "no":
        plan_forced = False

    out = cross_env.predict(prod_plan, dev_plan, float(dev_ms), dev_rates, prod_rates,
                            plan_forced=plan_forced, use_row_ratio=args.use_row_ratio,
                            calibration=args.calibration,
                            dev_speedup=dev_rec.get("parallel_speedup") or 1.0,
                            prod_speedup=prod_rec.get("parallel_speedup") or 1.0)

    if args.json:
        print(_json.dumps(out, indent=2, sort_keys=True, default=float))
        return 0

    print(f"CETP per-operator prediction ({args.dev} -> {args.prod})")
    print(f"  dev runtime:        {dev_ms:,.1f} ms")
    calib_note = ("" if out["calibration"] == 1.0 else f"  (incl. calibration x{args.calibration:.4f})")
    print(f"  scaling factor:     {out['scaling']:.4f}{calib_note}")
    print(f"  predicted prod:     {out['predicted_prod_ms']:,.1f} ms")
    print(f"  plan source:        {out['plan_source']}")
    print(f"  explained:          {out['explained_frac']:.0%} of dev self-time priced "
          f"by operator probes")
    print(f"  parallel speedup:   dev {out['dev_speedup']:.2f}x -> "
          f"prod {out['prod_speedup']:.2f}x")
    print("  per-axis capability ratio (dev/prod), weighted by dev wall-time:")
    for ax, w in sorted(out["weights_by_parallelism"].items(), key=lambda kv: -kv[1]):
        if w <= 0:
            continue
        r = out["per_axis_ratio"].get(ax)
        rs = f"{r:.3f}" if r is not None else "unpriced"
        print(f"    {ax:18s} weight {w:5.1%}   ratio {rs}")
    if args.sla_ms:
        verdict = "PASS" if out["predicted_prod_ms"] <= args.sla_ms else "BREACH"
        print(f"  SLA {args.sla_ms:,.0f} ms -> {verdict}")
    print(f"  trust:              {out['trust']}")
    for w in out["warnings"]:
        print(f"  ! {w}")
    return 0


def cmd_selftest(args):
    """End-to-end check that the installed package actually works."""
    fixtures = PROJECT_DIR / "tests" / "test_fixtures"
    plan_path = fixtures / "q1_local_real_explain.json"
    if not plan_path.exists():
        sys.exit(f"missing fixture {plan_path}")
    plan = json.loads(plan_path.read_text())
    ok = True

    red = _redact.redact_plan(plan)
    try:
        _redact.assert_clean(red)
        print("redaction          OK")
    except ValueError as e:
        ok = False
        print(f"redaction          FAIL: {e}")

    try:
        dev, prod = _resolve("c7i"), _resolve("c5a")
        r = _predict.predict(plan, dev, prod)
        print(f"prediction         OK  ({r['dev_time_ms']:.0f} ms dev -> "
              f"{r['predicted_ms']:.0f} ms prod, scaling {r['scaling_factor']:.3f})")
    except Exception as e:  # noqa: BLE001 - selftest reports any failure
        ok = False
        print(f"prediction         FAIL: {e}")

    try:
        rr = _regress.detect(plan, plan, _resolve("c7i"), _resolve("c5a"))
        assert abs(rr["predicted_prod_ratio"] - 1.0) < 1e-9, rr["predicted_prod_ratio"]
        assert rr["hardware_term_cancelled"]
        print("regression (identity) OK  (same plan twice -> ratio 1.000, "
              "hardware term cancels)")
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"regression         FAIL: {e}")

    try:
        _predict.load_calibration()
        print("calibration        OK")
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"calibration        FAIL: {e}")

    # Per-operator path. Checked with synthetic rates so selftest needs no live server:
    # identical rates must give scaling exactly 1.0, and a prod machine that grants no
    # parallelism must come out slower even when its per-worker rates are identical --
    # the case a serial-only signature got wrong by 64%.
    try:
        from . import cross_env as _cross
        from .operator_model import predict_scaling as _ps
        rates = {"seqscan": 1e7, "hashjoin": 6e6, "sort": 5e6,
                 "hashagg": 3e6, "nestloop": 1e6}
        par_plan = {"Execution Time": 100.0, "Plan": {
            "Node Type": "Gather", "Actual Total Time": 100.0, "Actual Loops": 1,
            "Actual Rows": 1, "Plan Rows": 1, "Plans": [{
                "Node Type": "Seq Scan", "Actual Total Time": 90.0, "Actual Loops": 3,
                "Actual Rows": 1, "Plan Rows": 1, "Parallel Aware": True}]}}
        same = _ps(par_plan, rates, rates, dev_speedup=2.5, prod_speedup=2.5)["scaling"]
        lost = _ps(par_plan, rates, rates, dev_speedup=2.5, prod_speedup=1.0)["scaling"]
        assert abs(same - 1.0) < 1e-9, f"identical machines gave {same}"
        assert lost > 2.0, f"lost parallelism gave only {lost}"
        out = _cross.predict(None, par_plan, 100.0, rates, rates, plan_forced=False)
        assert out["trust"] == "LOW", out["trust"]
        print(f"operator model     OK  (identical machines 1.000; prod without "
              f"parallelism {lost:.2f}x slower)")
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"operator model     FAIL: {e}")

    print("\nselftest " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


def build_parser():
    ap = argparse.ArgumentParser(
        prog="cetp", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    def add_machines(p):
        p.add_argument("--dev", help="dev machine id (see `cetp signature list`)")
        p.add_argument("--prod", help="prod machine id")
        p.add_argument("--dev-bandwidth", type=float)
        p.add_argument("--dev-compute", type=float)
        p.add_argument("--prod-bandwidth", type=float)
        p.add_argument("--prod-compute", type=float)
        p.add_argument("--predictor", choices=["analytical", "cmp_only"],
                       default=_predict.DEFAULT_PREDICTOR)
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("predict", help="predict production runtime from a dev-side plan")
    p.add_argument("--plan", required=True, help="EXPLAIN FORMAT JSON, or - for stdin")
    p.add_argument("--sla-ms", type=float)
    add_machines(p)
    p.set_defaults(func=cmd_predict)

    p = sub.add_parser("regress", help="detect a performance regression between two plans "
                                       "(recommended mode)")
    p.add_argument("--before", required=True)
    p.add_argument("--after", required=True)
    p.add_argument("--query-id", default="query")
    p.add_argument("--threshold", type=float, default=1.20)
    p.add_argument("--fail-on-regression", action="store_true",
                   help="exit 1 on REGRESSION, for CI")
    add_machines(p)
    p.set_defaults(func=cmd_regress)

    p = sub.add_parser("redact", help="strip literals from a plan before it leaves the machine")
    p.add_argument("plan")
    p.add_argument("-o", "--out", default="-")
    p.add_argument("--level", choices=["structural", "full"], default="structural")
    p.add_argument("--check", action="store_true")
    p.set_defaults(func=cmd_redact)

    p = sub.add_parser("signature", help="list or probe hardware signatures")
    p.add_argument("action", choices=["list", "probe"])
    p.add_argument("--machine-id")
    p.add_argument("--db", default="tpch")
    p.add_argument("--port", type=int, default=5432)
    p.set_defaults(func=cmd_signature)

    p = sub.add_parser("predict-prod",
                       help="per-operator prediction using production's own plan "
                            "(the four-move flow)")
    p.add_argument("--dev-plan", required=True,
                   help="development EXPLAIN (ANALYZE) FORMAT JSON, or - for stdin")
    p.add_argument("--prod-plan",
                   help="production EXPLAIN (no ANALYZE) FORMAT JSON; without it this "
                        "predicts the runtime of DEV's plan and says so")
    p.add_argument("--dev", required=True, help="dev machine id in the operator registry")
    p.add_argument("--prod", required=True, help="prod machine id in the operator registry")
    p.add_argument("--registry", help="operator signature registry "
                                      "(default models/operator_signature.json)")
    p.add_argument("--dev-runtime-ms", type=float,
                   help="override; defaults to the dev plan's Execution Time")
    p.add_argument("--plan-forced", choices=["yes", "no", "unknown"], default="unknown",
                   help="did dev actually run production's plan, verified?")
    p.add_argument("--calibration", type=float, default=1.0,
                   help="multiplicative correction for this prod machine (few-shot)")
    p.add_argument("--use-row-ratio", action="store_true",
                   help="apply the UNVALIDATED cardinality term from the plans' row counts")
    p.add_argument("--sla-ms", type=float)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_predict_prod)

    p = sub.add_parser("selftest", help="verify the installation end to end")
    p.set_defaults(func=cmd_selftest)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

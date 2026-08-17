import argparse
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import yaml

PROJECT_DIR = Path(__file__).resolve().parent
MODELS_DIR = PROJECT_DIR / "models"
RESULTS_DIR = PROJECT_DIR / "results"

RANDOM_STATE = 42
DEFAULT_CONFIDENCE_THRESHOLD = 0.6
RAW_BOTTLENECK_COLS = ["cost", "shared_hit", "shared_read", "total_buffers", "io_ratio", "rows"]


def load_models():
    bottleneck_model = joblib.load(MODELS_DIR / "bottleneck_classifier.pkl")
    quantile_models = joblib.load(MODELS_DIR / "scaling_quantile_models.pkl")
    hardware_signature = joblib.load(MODELS_DIR / "hardware_signature.pkl")
    feature_columns = json.loads((MODELS_DIR / "scaling_feature_columns.json").read_text())
    return bottleneck_model, quantile_models, hardware_signature, feature_columns


def root_buffers(plan_node, key):
    return plan_node.get(key, 0) or 0


def features_from_explain_json(explain_path):
    parsed = json.loads(Path(explain_path).read_text())
    plan = parsed[0]["Plan"]
    return {
        "cost": plan.get("Total Cost"),
        "time_ms": parsed[0].get("Execution Time"),
        "shared_hit": root_buffers(plan, "Shared Hit Blocks"),
        "shared_read": root_buffers(plan, "Shared Read Blocks"),
        "rows": plan.get("Actual Rows"),
    }


def features_from_json(features_path):
    return json.loads(Path(features_path).read_text())


def derive_dev_features(raw_features):
    cost = float(raw_features["cost"])
    shared_hit = float(raw_features["shared_hit"])
    shared_read = float(raw_features["shared_read"])
    rows = float(raw_features["rows"])
    time_ms = float(raw_features["time_ms"])
    total_buffers = shared_hit + shared_read
    io_ratio = shared_read / (total_buffers + 1)
    return {
        "cost": cost, "shared_hit": shared_hit, "shared_read": shared_read,
        "rows": rows, "time_ms": time_ms, "total_buffers": total_buffers, "io_ratio": io_ratio,
    }


def bottleneck_probabilities(bottleneck_model, dev_features):
    x = [[dev_features[col] for col in RAW_BOTTLENECK_COLS]]
    class_order = list(bottleneck_model.classes_)
    proba = bottleneck_model.predict_proba(x)[0]
    return {cls: float(p) for cls, p in zip(class_order, proba)}


def analytical_scaling(bandwidth_ratio, compute_ratio, p):
    inv_bandwidth = 1.0 / bandwidth_ratio
    inv_compute = 1.0 / compute_ratio
    mixed_term = np.sqrt(inv_bandwidth * inv_compute)
    w_bandwidth = p.get("bandwidth", 0.0) + p.get("io", 0.0)
    w_compute = p.get("compute", 0.0)
    w_mixed = p.get("mixed", 0.0)
    return float(w_bandwidth * inv_bandwidth + w_compute * inv_compute + w_mixed * mixed_term)


def predict_scaling(quantile_models, feature_columns, dev_features, bandwidth_ratio, compute_ratio, p, analytical):
    row_values = {
        "dev_cost": dev_features["cost"],
        "dev_total_buffers": dev_features["total_buffers"],
        "dev_shared_hit": dev_features["shared_hit"],
        "dev_shared_read": dev_features["shared_read"],
        "dev_rows": dev_features["rows"],
        "dev_io_ratio": dev_features["io_ratio"],
        "bandwidth_ratio": bandwidth_ratio,
        "compute_ratio": compute_ratio,
        "p_compute": p.get("compute", 0.0),
        "p_bandwidth": p.get("bandwidth", 0.0),
        "p_io": p.get("io", 0.0),
        "p_mixed": p.get("mixed", 0.0),
        "analytical_scaling": analytical,
    }
    x = np.array([[row_values[col] for col in feature_columns]])
    return {
        "p50": float(quantile_models[0.5].predict(x)[0]),
        "p95": float(quantile_models[0.95].predict(x)[0]),
        "p99": float(quantile_models[0.99].predict(x)[0]),
    }


def hardware_distance_confidence(prod_signature, hardware_signature_table):
    bandwidths = [v["bandwidth"] for v in hardware_signature_table.values()]
    computes = [v["compute"] for v in hardware_signature_table.values()]
    bw_min, bw_max = min(bandwidths), max(bandwidths)
    cp_min, cp_max = min(computes), max(computes)

    def normalize(bandwidth, compute):
        nb = (bandwidth - bw_min) / (bw_max - bw_min)
        nc = (compute - cp_min) / (cp_max - cp_min)
        return np.array([nb, nc])

    prod_point = normalize(prod_signature["bandwidth"], prod_signature["compute"])
    train_points = [normalize(v["bandwidth"], v["compute"]) for v in hardware_signature_table.values()]
    min_dist = min(float(np.linalg.norm(prod_point - t)) for t in train_points)
    confidence = max(0.0, 1.0 - min_dist / float(np.sqrt(2)))
    return float(confidence)


def three_state_verdict(dev_time_ms, scaling, confidence, confidence_threshold, sla_ms):
    predicted_prod_ms = {k: dev_time_ms * v for k, v in scaling.items()}
    low_confidence = confidence < confidence_threshold

    if low_confidence:
        state = "WARN"
        reason = (
            f"LOW CONFIDENCE (hw-distance confidence={confidence:.2f} < {confidence_threshold}): "
            "prod hardware signature is an outlier relative to the training set. "
            "Recommend a canary run or real measurement instead of trusting this prediction."
        )
    elif predicted_prod_ms["p50"] > sla_ms:
        state = "BLOCK"
        reason = f"predicted p50 ({predicted_prod_ms['p50']:.1f} ms) exceeds SLA ({sla_ms:.1f} ms)."
    elif predicted_prod_ms["p99"] <= sla_ms:
        state = "PASS"
        reason = f"predicted p99 ({predicted_prod_ms['p99']:.1f} ms) clears SLA ({sla_ms:.1f} ms)."
    else:
        state = "WARN"
        reason = (
            f"predicted p50 ({predicted_prod_ms['p50']:.1f} ms) is under SLA but predicted p99 "
            f"({predicted_prod_ms['p99']:.1f} ms) exceeds it ({sla_ms:.1f} ms). "
            "Recommend a canary run before deploying."
        )

    return {
        "state": state,
        "reason": reason,
        "predicted_prod_ms": predicted_prod_ms,
        "confidence": confidence,
        "low_confidence": low_confidence,
        "sla_ms": sla_ms,
    }


def run_gate_with_models(dev_features_raw, dev_signature, prod_signature, sla_ms,
                          bottleneck_model, quantile_models, hardware_signature_table, feature_columns,
                          confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD):
    if sla_ms <= 0:
        raise ValueError(f"sla_ms must be positive, got {sla_ms}")
    dev_features = derive_dev_features(dev_features_raw)

    bandwidth_ratio = prod_signature["bandwidth"] / dev_signature["bandwidth"]
    compute_ratio = prod_signature["compute"] / dev_signature["compute"]

    p = bottleneck_probabilities(bottleneck_model, dev_features)
    analytical = analytical_scaling(bandwidth_ratio, compute_ratio, p)
    scaling = predict_scaling(quantile_models, feature_columns, dev_features, bandwidth_ratio, compute_ratio, p, analytical)
    confidence = hardware_distance_confidence(prod_signature, hardware_signature_table)
    verdict = three_state_verdict(dev_features["time_ms"], scaling, confidence, confidence_threshold, sla_ms)

    return {
        "dev_features": dev_features,
        "dev_signature": dev_signature,
        "prod_signature": prod_signature,
        "bandwidth_ratio": bandwidth_ratio,
        "compute_ratio": compute_ratio,
        "bottleneck_probabilities": p,
        "analytical_scaling": analytical,
        "predicted_scaling_factor": scaling,
        "verdict": verdict,
    }


def run_gate(dev_features_raw, dev_signature, prod_signature, sla_ms, confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD):
    bottleneck_model, quantile_models, hardware_signature_table, feature_columns = load_models()
    return run_gate_with_models(
        dev_features_raw, dev_signature, prod_signature, sla_ms,
        bottleneck_model, quantile_models, hardware_signature_table, feature_columns,
        confidence_threshold,
    )


def format_report(query_id, result):
    v = result["verdict"]
    lines = [
        f"CETP Gate verdict for {query_id}: {v['state']}",
        f"  dev time: {result['dev_features']['time_ms']:.1f} ms  |  bandwidth_ratio={result['bandwidth_ratio']:.3f}  compute_ratio={result['compute_ratio']:.3f}",
        f"  bottleneck probs: " + ", ".join(f"{k}={p:.2f}" for k, p in result["bottleneck_probabilities"].items()),
        f"  predicted prod runtime: p50={v['predicted_prod_ms']['p50']:.1f} ms  p95={v['predicted_prod_ms']['p95']:.1f} ms  p99={v['predicted_prod_ms']['p99']:.1f} ms",
        f"  hw-distance confidence: {v['confidence']:.2f} (threshold {DEFAULT_CONFIDENCE_THRESHOLD})",
        f"  reason: {v['reason']}",
    ]
    return "\n".join(lines)


def load_config(config_path):
    return yaml.safe_load(Path(config_path).read_text())


def resolve_sla_ms(config, query_id, cli_sla_ms):
    if cli_sla_ms is not None:
        return cli_sla_ms
    sla_cfg = config["sla"]
    per_query = sla_cfg.get("per_query", {}) or {}
    if query_id and query_id in per_query:
        return float(per_query[query_id])
    return float(sla_cfg["default_ms"])


def resolve_signature(config, key, cli_bandwidth, cli_compute):
    if cli_bandwidth is not None and cli_compute is not None:
        return {"bandwidth": cli_bandwidth, "compute": cli_compute}
    hw = config[key]
    return {"bandwidth": float(hw["bandwidth_gbs"]), "compute": float(hw["compute_score"])}


def build_arg_parser():
    parser = argparse.ArgumentParser(description="CETP CI/CD SLA gate")
    parser.add_argument("--query-id", default=None)
    parser.add_argument("--dev-features")
    parser.add_argument("--explain-json")
    parser.add_argument("--config")
    parser.add_argument("--dev-bandwidth", type=float, default=None)
    parser.add_argument("--dev-compute", type=float, default=None)
    parser.add_argument("--prod-bandwidth", type=float, default=None)
    parser.add_argument("--prod-compute", type=float, default=None)
    parser.add_argument("--sla-ms", type=float, default=None)
    parser.add_argument("--confidence-threshold", type=float, default=None)
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON verdict")
    parser.add_argument("--demo", action="store_true", help="run the built-in worked demo and exit")
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)

    if args.demo:
        from cetp_gate_demo import run_demo
        run_demo()
        return 0

    if not args.dev_features and not args.explain_json:
        print("error: one of --dev-features or --explain-json is required", file=sys.stderr)
        return 2

    config = load_config(args.config) if args.config else None

    if args.explain_json:
        raw_features = features_from_explain_json(args.explain_json)
    else:
        raw_features = features_from_json(args.dev_features)

    if config is not None:
        dev_signature = resolve_signature(config, "dev_hardware", args.dev_bandwidth, args.dev_compute)
        prod_signature = resolve_signature(config, "prod_hardware", args.prod_bandwidth, args.prod_compute)
        sla_ms = resolve_sla_ms(config, args.query_id, args.sla_ms)
        confidence_threshold = args.confidence_threshold
        if confidence_threshold is None:
            confidence_threshold = float(config.get("confidence", {}).get("low_confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD))
    else:
        if args.dev_bandwidth is None or args.dev_compute is None or args.prod_bandwidth is None or args.prod_compute is None or args.sla_ms is None:
            print("error: without --config, --dev-bandwidth/--dev-compute/--prod-bandwidth/--prod-compute/--sla-ms are all required", file=sys.stderr)
            return 2
        dev_signature = {"bandwidth": args.dev_bandwidth, "compute": args.dev_compute}
        prod_signature = {"bandwidth": args.prod_bandwidth, "compute": args.prod_compute}
        sla_ms = args.sla_ms
        confidence_threshold = args.confidence_threshold if args.confidence_threshold is not None else DEFAULT_CONFIDENCE_THRESHOLD

    if sla_ms <= 0:
        print(f"error: --sla-ms must be positive, got {sla_ms}", file=sys.stderr)
        return 2

    result = run_gate(raw_features, dev_signature, prod_signature, sla_ms, confidence_threshold)
    query_id = args.query_id or "query"

    if args.json:
        print(json.dumps({"query_id": query_id, **result}, indent=2))
    else:
        print(format_report(query_id, result))

    return 1 if result["verdict"]["state"] == "BLOCK" else 0


if __name__ == "__main__":
    sys.exit(main())

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cetp_gate import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    load_config,
    resolve_sla_ms,
    resolve_signature,
    run_gate,
)

PSQL_TIMEOUT_SECONDS = 120


def strip_sql_comments(sql_text):
    lines = [line for line in sql_text.splitlines() if not line.strip().startswith("--")]
    return "\n".join(lines).strip()


def validate_query_file(query_file):
    path = Path(query_file)
    if not path.is_file():
        raise ValueError(f"query file not found: {query_file}")
    sql_text = strip_sql_comments(path.read_text())
    if not sql_text:
        raise ValueError(f"query file is empty: {query_file}")
    return sql_text


def run_explain(db, sql_text):
    cmd = ["psql", "-d", db, "-t", "-A", "-c", f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql_text}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=PSQL_TIMEOUT_SECONDS)
    except FileNotFoundError:
        raise RuntimeError("psql executable not found; is PostgreSQL installed and on PATH?")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"query timed out after {PSQL_TIMEOUT_SECONDS}s")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "psql exited with a non-zero status")
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError("psql did not return a parseable EXPLAIN JSON plan")
    return parsed


def features_from_explain(parsed):
    plan = parsed[0]["Plan"]
    return {
        "cost": plan.get("Total Cost"),
        "time_ms": parsed[0].get("Execution Time"),
        "shared_hit": plan.get("Shared Hit Blocks", 0) or 0,
        "shared_read": plan.get("Shared Read Blocks", 0) or 0,
        "rows": plan.get("Actual Rows"),
    }


def build_arg_parser():
    parser = argparse.ArgumentParser(description="Run a SQL file through EXPLAIN and the CETP gate, print a single JSON result")
    parser.add_argument("--query-file", required=True)
    parser.add_argument("--query-id", default=None)
    parser.add_argument("--db", default="tpch")
    parser.add_argument("--config")
    parser.add_argument("--dev-bandwidth", type=float, default=None)
    parser.add_argument("--dev-compute", type=float, default=None)
    parser.add_argument("--prod-bandwidth", type=float, required=True)
    parser.add_argument("--prod-compute", type=float, required=True)
    parser.add_argument("--sla-ms", type=float, default=None)
    parser.add_argument("--confidence-threshold", type=float, default=None)
    return parser


def emit(payload):
    print(json.dumps(payload))


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    query_id = args.query_id or Path(args.query_file).stem

    try:
        sql_text = validate_query_file(args.query_file)
    except ValueError as exc:
        emit({"status": "error", "stage": "validation", "query_id": query_id, "error": str(exc)})
        return 2

    if args.prod_bandwidth <= 0 or args.prod_compute <= 0:
        emit({"status": "error", "stage": "validation", "query_id": query_id, "error": "prod bandwidth/compute must be positive numbers"})
        return 2

    config = None
    if args.config:
        try:
            config = load_config(args.config)
        except Exception as exc:
            emit({"status": "error", "stage": "validation", "query_id": query_id, "error": f"could not load config: {exc}"})
            return 2

    if config is not None:
        dev_signature = resolve_signature(config, "dev_hardware", args.dev_bandwidth, args.dev_compute)
        sla_ms = resolve_sla_ms(config, query_id, args.sla_ms)
        confidence_threshold = args.confidence_threshold
        if confidence_threshold is None:
            confidence_threshold = float(config.get("confidence", {}).get("low_confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD))
    else:
        if args.dev_bandwidth is None or args.dev_compute is None or args.sla_ms is None:
            emit({"status": "error", "stage": "validation", "query_id": query_id, "error": "without --config, --dev-bandwidth/--dev-compute/--sla-ms are required"})
            return 2
        dev_signature = {"bandwidth": args.dev_bandwidth, "compute": args.dev_compute}
        sla_ms = args.sla_ms
        confidence_threshold = args.confidence_threshold if args.confidence_threshold is not None else DEFAULT_CONFIDENCE_THRESHOLD

    prod_signature = {"bandwidth": args.prod_bandwidth, "compute": args.prod_compute}

    try:
        explain_parsed = run_explain(args.db, sql_text)
        raw_features = features_from_explain(explain_parsed)
        result = run_gate(raw_features, dev_signature, prod_signature, sla_ms, confidence_threshold)
    except RuntimeError as exc:
        emit({"status": "error", "stage": "backend", "query_id": query_id, "error": str(exc)})
        return 3
    except Exception as exc:
        emit({"status": "error", "stage": "backend", "query_id": query_id, "error": f"{type(exc).__name__}: {exc}"})
        return 3

    v = result["verdict"]
    payload = {
        "status": "ok",
        "query_id": query_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dev_time_ms": result["dev_features"]["time_ms"],
        "bandwidth_ratio": result["bandwidth_ratio"],
        "compute_ratio": result["compute_ratio"],
        "bottleneck_probabilities": result["bottleneck_probabilities"],
        "predicted_prod_ms": v["predicted_prod_ms"],
        "confidence": v["confidence"],
        "verdict": v["state"],
        "reason": v["reason"],
        "sla_ms": sla_ms,
    }
    emit(payload)
    return 1 if v["state"] == "BLOCK" else 0


if __name__ == "__main__":
    sys.exit(main())

import json
from pathlib import Path

from cetp_gate import run_gate, format_report, PROJECT_DIR, RESULTS_DIR

EXAMPLES_DIR = PROJECT_DIR / "examples" / "gate_queries"

SCENARIOS = [
    {
        "label": "PASS",
        "query_id": "q19 (c5a -> c7i)",
        "dev_features_path": EXAMPLES_DIR / "q19_c5a_dev_features.json",
        "dev_signature": {"bandwidth": 14.9, "compute": 0.593},
        "prod_signature": {"bandwidth": 8.44, "compute": 0.807},
        "sla_ms": 200.0,
        "note": "Known dev/prod machines, generous SLA relative to the predicted tail.",
    },
    {
        "label": "BLOCK",
        "query_id": "q1 (r5n -> z1d)",
        "dev_features_path": EXAMPLES_DIR / "q1_r5n_dev_features.json",
        "dev_signature": {"bandwidth": 9.39, "compute": 0.705},
        "prod_signature": {"bandwidth": 9.77, "compute": 0.882},
        "sla_ms": 3000.0,
        "note": "Known dev/prod machines, predicted p50 already exceeds SLA.",
    },
    {
        "label": "WARN (in-between)",
        "query_id": "q18 (c7i -> r5n)",
        "dev_features_path": EXAMPLES_DIR / "q18_c7i_dev_features.json",
        "dev_signature": {"bandwidth": 8.44, "compute": 0.807},
        "prod_signature": {"bandwidth": 9.39, "compute": 0.705},
        "sla_ms": 8000.0,
        "note": "Known dev/prod machines, SLA sits between predicted p50 and p99.",
    },
    {
        "label": "WARN (low confidence)",
        "query_id": "q19 (c7i -> hypothetical high-bandwidth prod tier)",
        "dev_features_path": EXAMPLES_DIR / "q19_c7i_dev_features.json",
        "dev_signature": {"bandwidth": 8.44, "compute": 0.807},
        "prod_signature": {"bandwidth": 30.0, "compute": 1.4},
        "sla_ms": 100.0,
        "note": "Prod signature is far outside the 5-machine training envelope (bandwidth in [8.44, 14.9], compute in [0.457, 0.882]).",
    },
]


def run_demo():
    report_lines = ["# Phase 8 CETP Gate — Worked Demo\n"]
    print("=" * 90)
    print("CETP GATE — WORKED DEMO (4 scenarios covering every verdict branch)")
    print("=" * 90)

    for scenario in SCENARIOS:
        dev_features_raw = json.loads(scenario["dev_features_path"].read_text())
        result = run_gate(
            dev_features_raw,
            scenario["dev_signature"],
            scenario["prod_signature"],
            scenario["sla_ms"],
        )
        header = f"\n--- Scenario: {scenario['label']} | {scenario['query_id']} | SLA={scenario['sla_ms']} ms ---"
        note = f"({scenario['note']})"
        body = format_report(scenario["query_id"], result)
        print(header)
        print(note)
        print(body)

        report_lines.append(f"## {scenario['label']} — {scenario['query_id']}\n")
        report_lines.append(f"SLA threshold: {scenario['sla_ms']} ms\n")
        report_lines.append(f"{note}\n")
        report_lines.append("```")
        report_lines.append(body)
        report_lines.append("```\n")

    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / "phase8_cetp_gate_demo.md"
    out_path.write_text("\n".join(report_lines))
    print(f"\nSaved demo report to {out_path}")


if __name__ == "__main__":
    run_demo()

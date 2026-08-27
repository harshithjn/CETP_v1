#!/usr/bin/env python3
"""Test CETP predictions against real measurements to show accuracy."""

import sys
sys.path.insert(0, 'gate')

import json
from pathlib import Path
import cetp_gate

print('=' * 70)
print('LIVE PREDICTION ACCURACY TEST')
print('=' * 70)
print()
print('Testing predictions against real measurements from the dataset...')
print()

# Example test cases with known actual results
test_cases = [
    {
        "query": "q19",
        "dev_machine": "c5a", 
        "prod_machine": "c7i",
        "dev_features_file": "gate/examples/gate_queries/q19_c5a_dev_features.json",
        "actual_prod_time": 53.2,  # From the dataset
        "description": "Bandwidth-bound query, prod has slower bandwidth"
    },
    {
        "query": "q19",
        "dev_machine": "c7i",
        "prod_machine": "c5a", 
        "dev_features_file": "gate/examples/gate_queries/q19_c7i_dev_features.json",
        "actual_prod_time": 43.7,  # From the dataset
        "description": "Same query reversed, prod has faster bandwidth"
    },
    {
        "query": "q1",
        "dev_machine": "r5n",
        "prod_machine": "z1d",
        "dev_features_file": "gate/examples/gate_queries/q1_r5n_dev_features.json",
        "actual_prod_time": 9215.0,  # Compute-bound query
        "description": "Compute-bound query, checking compute ratio"
    }
]

# Hardware signatures (from models/hardware_signature.pkl)
hardware_sigs = {
    "c5a": {"bandwidth": 14.9, "compute": 0.593},
    "c7i": {"bandwidth": 8.44, "compute": 0.807},
    "m5a": {"bandwidth": 9.78, "compute": 0.636},
    "r5n": {"bandwidth": 13.3, "compute": 0.605},
    "z1d": {"bandwidth": 13.8, "compute": 0.482}
}

print(f'{"Query":<8} {"Dev→Prod":<12} {"Dev Time":<10} {"Actual":<10} {"Predicted":<10} {"Error":<10} {"Type":<15}')
print('=' * 90)

total_mape = 0
num_tests = 0

for test in test_cases:
    # Load dev features
    dev_features_raw = json.loads(Path(test["dev_features_file"]).read_text())
    
    # Get hardware signatures
    dev_sig = hardware_sigs[test["dev_machine"]]
    prod_sig = hardware_sigs[test["prod_machine"]]
    
    # Run prediction
    result = cetp_gate.run_gate(
        dev_features_raw, 
        dev_sig, 
        prod_sig, 
        sla_ms=10000  # High SLA, we just want the prediction
    )
    
    # Extract values
    dev_time = result["dev_features"]["time_ms"]
    predicted_prod_p50 = result["verdict"]["predicted_prod_ms"]["p50"]
    predicted_prod_p99 = result["verdict"]["predicted_prod_ms"]["p99"]
    actual_prod = test["actual_prod_time"]
    
    # Calculate error (using p50 prediction)
    error_pct = abs(predicted_prod_p50 - actual_prod) / actual_prod * 100
    
    # Determine bottleneck type
    bottleneck = max(result["bottleneck_probabilities"].items(), key=lambda x: x[1])[0]
    
    route = f'{test["dev_machine"]}→{test["prod_machine"]}'
    
    print(f'{test["query"]:<8} {route:<12} {dev_time:>8.1f}ms {actual_prod:>8.1f}ms {predicted_prod_p50:>8.1f}ms {error_pct:>8.1f}% {bottleneck:<15}')
    
    total_mape += error_pct
    num_tests += 1

print('=' * 90)
print(f'\nAverage Error (MAPE): {total_mape/num_tests:.2f}%')
print()

print('=' * 70)
print('PREDICTION BREAKDOWN FOR FIRST TEST CASE:')
print('=' * 70)
print()

# Detailed breakdown for first test
test = test_cases[0]
dev_features_raw = json.loads(Path(test["dev_features_file"]).read_text())
dev_sig = hardware_sigs[test["dev_machine"]]
prod_sig = hardware_sigs[test["prod_machine"]]

result = cetp_gate.run_gate(dev_features_raw, dev_sig, prod_sig, sla_ms=10000)

print(f'Query: {test["query"]} ({test["description"]})')
print(f'Development machine: {test["dev_machine"]} (bandwidth={dev_sig["bandwidth"]} GB/s, compute={dev_sig["compute"]})')
print(f'Production machine:  {test["prod_machine"]} (bandwidth={prod_sig["bandwidth"]} GB/s, compute={prod_sig["compute"]})')
print()
print(f'Development time: {result["dev_features"]["time_ms"]:.1f} ms')
print()
print('Hardware ratios:')
print(f'  bandwidth_ratio: {result["bandwidth_ratio"]:.3f} (prod/dev)')
print(f'  compute_ratio:   {result["compute_ratio"]:.3f} (prod/dev)')
print()
print('Bottleneck classification:')
for bottleneck, prob in result["bottleneck_probabilities"].items():
    print(f'  {bottleneck:10s}: {prob:.2f}')
print()
print('Predictions:')
print(f'  p50 (median):  {result["verdict"]["predicted_prod_ms"]["p50"]:.1f} ms')
print(f'  p95:           {result["verdict"]["predicted_prod_ms"]["p95"]:.1f} ms')
print(f'  p99 (worst):   {result["verdict"]["predicted_prod_ms"]["p99"]:.1f} ms')
print()
print(f'Actual production time: {test["actual_prod_time"]:.1f} ms')
print(f'Prediction error (p50): {abs(result["verdict"]["predicted_prod_ms"]["p50"] - test["actual_prod_time"]) / test["actual_prod_time"] * 100:.1f}%')
print(f'Confidence: {result["verdict"]["confidence"]:.2f}')
print()

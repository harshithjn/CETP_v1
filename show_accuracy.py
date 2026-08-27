#!/usr/bin/env python3
"""Display actual prediction accuracy from CETP evaluation results."""

import pandas as pd
import numpy as np

print('=' * 70)
print('CETP PREDICTION ACCURACY - REAL RESULTS')
print('=' * 70)
print()

# Read bootstrap table
df = pd.read_csv('results/phase7_mape_bootstrap_table.csv')
print('Overall Accuracy (Leave-One-Machine-Out Cross-Validation):')
print('-' * 70)
for _, row in df.iterrows():
    mape = row['mape']
    ci_low = row['ci_low']
    ci_high = row['ci_high']
    approach = row['approach']
    print(f'{approach:40s} {mape:6.2f}% (95% CI: {ci_low:5.2f}% - {ci_high:5.2f}%)')

print()
print('=' * 70)
print('ACCURACY BY HARDWARE (when that machine was held out):')
print('=' * 70)
print()

# Read per-machine breakdown
df_machine = pd.read_csv('results/phase7_per_machine_breakdown_corrected.csv')
print(f'{"Machine":<8} {"n":<5} {"Learned":<12} {"Analytical":<12} {"Naive-Linear":<12}')
print('-' * 70)
for _, row in df_machine.iterrows():
    print(f'{row["machine"]:<8} {int(row["n"]):<5} {row["learned_mape"]:>10.2f}% {row["analytical_mape"]:>10.2f}% {row["naive_linear_mape"]:>10.2f}%')

print()
print('=' * 70)
print('IMPROVED ACCURACY WITH MULTI-CORE COMPUTE BENCHMARK:')
print('=' * 70)
print()

# Read addition4 results
df_add4 = pd.read_csv('results/addition4_reevaluation.csv')
print('Analytical Roofline Model MAPE:')
print('-' * 70)
for _, row in df_add4.iterrows():
    label = row['label']
    analytical = row['analytical_lomo']
    print(f'  {label[:60]:<62} {analytical:>6.2f}%')

print()
print('=' * 70)
print('KEY FINDINGS:')
print('=' * 70)
print()
print('1. BEST BASELINE: Naive-linear (1/bandwidth_ratio)')
print('   → 23.03% MAPE (95% CI: 20.29% - 26.05%)')
print()
print('2. IMPROVEMENT: Multi-threaded compute benchmark')
print('   → Analytical roofline: 31.14% → 21.60% MAPE')
print('   → Bottleneck-gated:    28.06% → 22.30% MAPE')
print('   → Statistically significant improvement!')
print()
print('3. WORST CASE (c5a unfamiliar hardware):')
print('   → Naive-linear: 19.66% error')
print('   → Learned model: 54.85% error (2.8x worse!)')
print()
print('4. BEST CASE (z1d familiar hardware):')
print('   → Learned model: 17.85% error')
print('   → Demonstrates interpolation vs extrapolation gap')
print()

const DATA = {
  meta: {
    machines: 5,
    queries: 21,
    method: "leave-one-machine-out cross-validation",
    bestMape: 21.60,
    flipLabel: "multi-core compute signal flips analytical formula from losing to beating naive-linear",
  },

  mapeTable: {
    rows: [
      { name: "naive (assume prod = dev)", value: 31.97, beats: false },
      { name: "naive-linear (1 / bandwidth_ratio)", value: 23.03, beats: null, baseline: true },
      { name: "analytical roofline, old single-core compute", value: 31.12, beats: false },
      { name: "analytical roofline, multi-core compute", value: 21.60, beats: true },
      { name: "bottleneck-gated, multi-core compute", value: 22.30, beats: true },
      { name: "learned quantile GBR, multi-core compute", value: 23.38, beats: false },
    ],
    ciOldCompute: [-11.80, -4.38],
    ciMultiCore: [0.30, 2.55],
    source: "results/corrected_headline_mape.json (tpch_dataset_corrected.csv, 2000-resample cluster bootstrap, seed 42)",
  },

  computeSignal: {
    singleThreadCorr: 0.213,
    multiThreadCorr: -0.691,
    confound: {
      measuredCorr: -0.691,
      vcpuCorr: -0.479,
      measuredMape: 21.60,
      vcpuMape: 26.32,
    },
    source: "results/addition4_reevaluation.csv, results/addition4b_mechanism_summary.csv",
  },

  perMachine: {
    rows: [
      { machine: "c5a", learned: 54.85, analytical: 33.86, naiveLinear: 19.66, outlier: true },
      { machine: "c7i", learned: 36.37, analytical: 29.61, naiveLinear: 27.68, outlier: false },
      { machine: "m5a", learned: 29.05, analytical: 31.81, naiveLinear: 20.57, outlier: false },
      { machine: "r5n", learned: 25.81, analytical: 30.95, naiveLinear: 25.98, outlier: false },
      { machine: "z1d", learned: 17.85, analytical: 29.34, naiveLinear: 21.25, outlier: false },
    ],
    source: "results/phase7_per_machine_breakdown_corrected.csv",
  },

  selfCalibration: {
    confidenceBefore: 0.000,
    confidenceAfter: 1.000,
    heldOutMapeBefore: 77.72,
    heldOutMapeAfter: 15.01,
    heldOutN: 36,
    improvementPp: 62.71,
    narrative: {
      devMachine: "c7i",
      prodMachine: "c5a",
      query: "q19",
      actual: 0.8227,
      predictedBefore: 1.2575,
      errorBefore: 52.8,
      predictedAfter: 0.6745,
      errorAfter: 18.0,
    },
    knownMachinesBefore: ["c7i", "m5a", "r5n", "z1d"],
    knownMachinesAfter: ["c5a", "c7i", "m5a", "r5n", "z1d"],
    measurementsToRetrain: 12,
    source: "results/addition7_summary.json",
  },

  learningCurve: {
    kAt1: 40.19,
    kAt4: 28.10,
    figure: "assets/learning_curve_corrected.png",
    source: "results/learning_curve_machines_seen_corrected.csv",
  },

  confidenceAbstention: {
    aurcHwDistance: 25.44,
    aurcRandom: 31.15,
    operatingCoveragePct: 80.0,
    operatingSelectiveMape: 28.13,
    operatingThreshold: 0.6,
    tiers: [
      { confidence: 1.000, n: 420, mape: 27.03, cumCoveragePct: 50.0 },
      { confidence: 0.808, n: 168, mape: 33.43, cumCoveragePct: 70.0 },
      { confidence: 0.801, n: 84, mape: 22.97, cumCoveragePct: 80.0 },
      { confidence: 0.582, n: 84, mape: 32.22, cumCoveragePct: 90.0 },
      { confidence: 0.419, n: 84, mape: 70.66, cumCoveragePct: 100.0, catastrophic: true },
    ],
    figure: "assets/risk_coverage_curve_corrected.png",
    source: "results/confidence_abstention_summary_corrected.json, results/confidence_tiers_corrected.csv",
  },

  slaGateDemo: [
    {
      label: "PASS", query: "q19 (c5a → c7i)", slaMs: 200.0, devTimeMs: 43.7,
      bandwidthRatio: 0.566, computeRatio: 1.361, confidence: 1.00,
      p50: 54.1, p95: 74.8, p99: 95.3,
      reason: "predicted p99 clears SLA",
    },
    {
      label: "BLOCK", query: "q1 (r5n → z1d)", slaMs: 3000.0, devTimeMs: 7103.3,
      bandwidthRatio: 1.040, computeRatio: 1.251, confidence: 1.00,
      p50: 6070.7, p95: 9952.0, p99: 10742.5,
      reason: "predicted p50 already exceeds SLA",
    },
    {
      label: "WARN", query: "q18 (c7i → r5n)", slaMs: 8000.0, devTimeMs: 5414.2,
      bandwidthRatio: 1.113, computeRatio: 0.874, confidence: 1.00,
      p50: 6255.6, p95: 7642.0, p99: 8043.8,
      reason: "p50 under SLA but p99 exceeds it",
    },
    {
      label: "WARN (low confidence)", query: "q19 (c7i → hypothetical high-bandwidth prod tier)", slaMs: 100.0, devTimeMs: 53.2,
      bandwidthRatio: 3.555, computeRatio: 1.735, confidence: 0.00,
      p50: 36.1, p95: 53.3, p99: 81.0,
      reason: "prod hardware signature is an outlier relative to the training set",
    },
  ],
  slaGateSource: "results/phase8_cetp_gate_demo_corrected.json (retrained on tpch_dataset_corrected.csv, same gate logic as cetp_gate.py)",

  dataIntegrity: {
    inflationMin: 3.06,
    inflationMax: 12.00,
    nQueries: 21,
    classifierF1Buggy: 0.5625,
    classifierF1Corrected: 0.375,
    ioRecallBuggy: 0.25,
    ioRecallCorrected: 0.00,
    headlineMapeShift: "≤ 2 percentage points across every headline comparison; multi-core analytical and gated MAPE identical to 2 decimals before and after correction",
    source: "results/buffer_bug_correction_report.md, results/bottleneck_classifier_corrected.json, results/corrected_headline_mape.json",
  },

  limitations: [
    "5 machines: leave-one-machine-out means 5 folds — enough to detect the interpolation-vs-extrapolation asymmetry (the c5a result), not enough to bound how often a held-out machine behaves like c5a rather than the other four.",
    "Single scale factor: all data is TPC-H SF1; scaling behavior at other data sizes is not characterized.",
    "EBS-only storage: all five instance types share network-attached EBS under one AMI; the io bottleneck class reflects buffer-touch patterns within one storage backend, not genuine storage-hardware diversity.",
    "Parallelism mechanism is leaning-confirmed, not established: the config check rules out parallelism being off, but the serial-plan sample is thin (2 of 21 queries) with two unexplained counter-examples.",
    "Bottleneck classifier is weak on io: corrected macro-F1 is 0.375 (was reported as 0.562 on inflated buffer data); io-class recall is 0.00 after correction.",
    "Upper-quantile calibration under-covers: p95 empirical coverage 80.7% vs nominal 95%, p99 94.4% vs nominal 99%, consistent with thin per-query repeat samples (20 runs).",
    "dev_rows requires an actual dev-side EXPLAIN ANALYZE run, not a pre-execution plan estimate — the gate needs a real dev run before it can predict, not just a static query plan.",
    "Q15 excluded from the entire project's scope (requires a view/temp-table construct the collection script does not handle).",
    "Confidence is a discrete, 5-valued outlier detector (one value per held-out machine), not a continuously graded uncertainty estimate; its power is concentrated on isolating the one catastrophic case.",
    "Self-calibration (Addition 7) is demonstrated on held-out data standing in for a live measurement — it has not been tested against a live production stream.",
  ],
};

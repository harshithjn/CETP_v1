# Phase 8 CETP Gate — Worked Demo

## PASS — q19 (c5a -> c7i)

SLA threshold: 200.0 ms

(Known dev/prod machines, generous SLA relative to the predicted tail.)

```
CETP Gate verdict for q19 (c5a -> c7i): PASS
  dev time: 43.7 ms  |  bandwidth_ratio=0.566  compute_ratio=1.361
  bottleneck probs: bandwidth=0.99, compute=0.00, io=0.01, mixed=0.00
  predicted prod runtime: p50=59.1 ms  p95=79.9 ms  p99=129.6 ms
  hw-distance confidence: 1.00 (threshold 0.6)
  reason: predicted p99 (129.6 ms) clears SLA (200.0 ms).
```

## BLOCK — q1 (r5n -> z1d)

SLA threshold: 3000.0 ms

(Known dev/prod machines, predicted p50 already exceeds SLA.)

```
CETP Gate verdict for q1 (r5n -> z1d): BLOCK
  dev time: 7103.3 ms  |  bandwidth_ratio=1.040  compute_ratio=1.251
  bottleneck probs: bandwidth=0.01, compute=0.99, io=0.00, mixed=0.00
  predicted prod runtime: p50=6104.4 ms  p95=10539.3 ms  p99=42616.8 ms
  hw-distance confidence: 1.00 (threshold 0.6)
  reason: predicted p50 (6104.4 ms) exceeds SLA (3000.0 ms).
```

## WARN (in-between) — q18 (c7i -> r5n)

SLA threshold: 8000.0 ms

(Known dev/prod machines, SLA sits between predicted p50 and p99.)

```
CETP Gate verdict for q18 (c7i -> r5n): WARN
  dev time: 5414.2 ms  |  bandwidth_ratio=1.113  compute_ratio=0.874
  bottleneck probs: bandwidth=0.00, compute=0.00, io=0.00, mixed=1.00
  predicted prod runtime: p50=6157.2 ms  p95=8010.4 ms  p99=12830.3 ms
  hw-distance confidence: 1.00 (threshold 0.6)
  reason: predicted p50 (6157.2 ms) is under SLA but predicted p99 (12830.3 ms) exceeds it (8000.0 ms). Recommend a canary run before deploying.
```

## WARN (low confidence) — q19 (c7i -> hypothetical high-bandwidth prod tier)

SLA threshold: 100.0 ms

(Prod signature is far outside the 5-machine training envelope (bandwidth in [8.44, 14.9], compute in [0.457, 0.882]).)

```
CETP Gate verdict for q19 (c7i -> hypothetical high-bandwidth prod tier): WARN
  dev time: 53.2 ms  |  bandwidth_ratio=3.555  compute_ratio=1.735
  bottleneck probs: bandwidth=0.99, compute=0.00, io=0.01, mixed=0.00
  predicted prod runtime: p50=35.3 ms  p95=69.3 ms  p99=104.5 ms
  hw-distance confidence: 0.00 (threshold 0.6)
  reason: LOW CONFIDENCE (hw-distance confidence=0.00 < 0.6): prod hardware signature is an outlier relative to the training set. Recommend a canary run or real measurement instead of trusting this prediction.
```

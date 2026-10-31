# Phase 8 CETP Gate — Worked Demo

## PASS — q19 (c5a -> c7i)

SLA threshold: 200.0 ms

(Known dev/prod machines, generous SLA relative to the predicted tail.)

```
CETP Gate verdict for q19 (c5a -> c7i): PASS
  dev time: 43.7 ms  |  bandwidth_ratio=0.566  compute_ratio=0.634
  bottleneck probs: bandwidth=0.39, compute=0.33, io=0.22, mixed=0.06
  predicted prod runtime: p50=63.2 ms  p95=78.7 ms  p99=89.1 ms
  hw-distance confidence: 1.00 (threshold 0.6)
  reason: predicted p99 (89.1 ms) clears SLA (200.0 ms).
```

## BLOCK — q1 (r5n -> z1d)

SLA threshold: 3000.0 ms

(Known dev/prod machines, predicted p50 already exceeds SLA.)

```
CETP Gate verdict for q1 (r5n -> z1d): BLOCK
  dev time: 7103.3 ms  |  bandwidth_ratio=1.040  compute_ratio=1.125
  bottleneck probs: bandwidth=0.15, compute=0.85, io=0.00, mixed=0.00
  predicted prod runtime: p50=5951.4 ms  p95=10636.4 ms  p99=13561.2 ms
  hw-distance confidence: 1.00 (threshold 0.6)
  reason: predicted p50 (5951.4 ms) exceeds SLA (3000.0 ms).
```

## WARN (in-between) — q18 (c7i -> r5n)

SLA threshold: 8000.0 ms

(Known dev/prod machines, SLA sits between predicted p50 and p99.)

```
CETP Gate verdict for q18 (c7i -> r5n): WARN
  dev time: 5414.2 ms  |  bandwidth_ratio=1.113  compute_ratio=0.964
  bottleneck probs: bandwidth=0.00, compute=0.00, io=0.13, mixed=0.87
  predicted prod runtime: p50=6595.5 ms  p95=7878.2 ms  p99=10048.0 ms
  hw-distance confidence: 1.00 (threshold 0.6)
  reason: predicted p50 (6595.5 ms) is under SLA but predicted p99 (10048.0 ms) exceeds it (8000.0 ms). Recommend a canary run before deploying.
```

## WARN (low confidence) — q19 (c7i -> hypothetical high-bandwidth prod tier)

SLA threshold: 100.0 ms

(Prod signature is far outside the 5-machine training envelope (bandwidth in [8.44, 14.9], multi-core compute in [24.97, 40.81]).)

```
CETP Gate verdict for q19 (c7i -> hypothetical high-bandwidth prod tier): WARN
  dev time: 53.2 ms  |  bandwidth_ratio=3.555  compute_ratio=3.476
  bottleneck probs: bandwidth=0.39, compute=0.33, io=0.22, mixed=0.06
  predicted prod runtime: p50=34.6 ms  p95=49.6 ms  p99=104.6 ms
  hw-distance confidence: 0.00 (threshold 0.6)
  reason: LOW CONFIDENCE (hw-distance confidence=0.00 < 0.6): prod hardware signature is an outlier relative to the training set. Recommend a canary run or real measurement instead of trusting this prediction.
```

export interface ProdProfile {
  name: string;
  bandwidth: number;
  compute: number;
}

export interface PredictionOk {
  status: 'ok';
  query_id: string;
  timestamp: string;
  dev_time_ms: number;
  bandwidth_ratio: number;
  compute_ratio: number;
  bottleneck_probabilities: Record<string, number>;
  predicted_prod_ms: { p50: number; p95: number; p99: number };
  confidence: number;
  verdict: 'PASS' | 'BLOCK' | 'WARN';
  reason: string;
  sla_ms: number;
}

export interface PredictionError {
  status: 'error';
  stage: 'validation' | 'backend';
  query_id: string;
  error: string;
}

export type PredictionResult = PredictionOk | PredictionError;

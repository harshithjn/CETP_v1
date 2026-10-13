"""
Production evaluation telemetry: log what was predicted, then what actually happened.

WHY. Every accuracy figure in this project comes from an offline experiment on TPC-H. None
of it tells you how the predictor behaves on YOUR workload, and the measured history says
that gap matters: the same model scores 29.31% within one CPU architecture and 68.92%
across one. A deployment that cannot see its own error is a deployment that will not notice
when it drifts into the second regime.

WHAT IT RECORDS. One line per prediction (JSON Lines, append-only), and later one
resolution when the real duration is known. Nothing else: no query text, no literals, no
plan. A query id and the two machine ids are enough to compute error and to feed
cetp.calibration_store.

THE LOOP THIS CLOSES.

    predict  ->  record_prediction(...)        a row with actual_ms = null
    later    ->  resolve(query_id, actual_ms)  from auto_explain's reported duration
    then     ->  summary()                     measured MAPE on YOUR workload
             ->  feed the same pair into CalibrationStore, and the correction improves

That is the sense in which the system gets better with use, and it is deliberately the only
sense claimed: accuracy improves for an environment PAIR as observations of that pair
accumulate. It does not improve because other machines exist.

WHY JSON LINES. Append-only means a crash mid-write costs one line, not the file; it needs
no database; and it is trivially greppable. Resolution rewrites the file, which is fine at
the volume a human workload produces and is explicitly not suitable for high-frequency
automated logging -- at that point this should be a real table.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

DEFAULT_PATH = Path(
    os.environ.get("CETP_TELEMETRY_PATH", Path.home() / ".cetp" / "telemetry.jsonl"))

# Fields that must never be written, even if a caller passes them. Telemetry that leaks the
# query is worse than no telemetry: cetp.redact exists precisely because EXPLAIN output
# embeds user data, and it would be absurd to strip it from plans and then log it here.
_FORBIDDEN = {"query_text", "query", "sql", "statement", "plan", "filter", "literals"}


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Telemetry:
    """Append-only prediction log with later resolution against measured runtime."""

    def __init__(self, path=None):
        self.path = Path(path) if path else DEFAULT_PATH

    # ------------------------------------------------------------------ write

    def record_prediction(self, query_id, dev, prod, predicted_ms, dev_ms=None,
                          scaling=None, trust=None, calibration=None,
                          cross_architecture=None, prod_clients=1, extra=None):
        """Append one prediction. Returns the row written."""
        if extra:
            bad = _FORBIDDEN & {k.lower() for k in extra}
            if bad:
                raise ValueError(
                    f"refusing to log {sorted(bad)}: telemetry must not contain query text "
                    f"or plans. Log identifiers and metrics only.")
        row = {
            "ts": _now(), "query_id": str(query_id), "dev": dev, "prod": prod,
            "predicted_ms": float(predicted_ms),
            "dev_ms": float(dev_ms) if dev_ms is not None else None,
            "scaling": float(scaling) if scaling is not None else None,
            "trust": trust,
            "calibration": float(calibration) if calibration is not None else None,
            "cross_architecture": cross_architecture,
            "prod_clients": int(prod_clients),
            "actual_ms": None, "resolved_at": None,
        }
        if extra:
            row.update(extra)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
        return row

    def resolve(self, query_id, actual_ms, dev=None, prod=None):
        """
        Attach the measured runtime to the most recent unresolved prediction that matches.

        Most recent, not all: a query predicted repeatedly should accumulate one
        observation per run, not have its whole history overwritten by the newest number.
        """
        rows = self.rows()
        target = None
        for r in reversed(rows):
            if r.get("actual_ms") is not None or str(r.get("query_id")) != str(query_id):
                continue
            if dev and r.get("dev") != dev:
                continue
            if prod and r.get("prod") != prod:
                continue
            target = r
            break
        if target is None:
            return None
        target["actual_ms"] = float(actual_ms)
        target["resolved_at"] = _now()
        self._rewrite(rows)
        return target

    def _rewrite(self, rows):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                for r in rows:
                    fh.write(json.dumps(r, sort_keys=True) + "\n")
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------ read

    def rows(self):
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue          # one bad line must not destroy the history
        return out

    def resolved(self):
        return [r for r in self.rows()
                if r.get("actual_ms") and r.get("predicted_ms")]

    def summary(self, dev=None, prod=None):
        """
        Measured error on the workload actually seen -- not on TPC-H.

        Reports the naive baseline alongside, because "29% MAPE" means nothing without
        knowing what assuming prod == dev would have scored on the same queries. That
        comparison is the whole point: on four x86 machines the model scored 68.9% while
        the baseline scored 21.7%, and a deployment needs to be able to notice that.
        """
        rows = [r for r in self.resolved()
                if (not dev or r["dev"] == dev) and (not prod or r["prod"] == prod)]
        if not rows:
            return {"n": 0, "mape": None, "naive_mape": None, "beats_naive": None,
                    "note": "no resolved predictions yet"}
        errs, naive_errs = [], []
        for r in rows:
            a = r["actual_ms"]
            if not a:
                continue
            errs.append(abs(r["predicted_ms"] - a) / a * 100)
            if r.get("dev_ms"):
                naive_errs.append(abs(r["dev_ms"] - a) / a * 100)
        mape = sum(errs) / len(errs) if errs else None
        naive = sum(naive_errs) / len(naive_errs) if naive_errs else None
        return {
            "n": len(errs), "mape": mape, "naive_mape": naive,
            "n_with_naive": len(naive_errs),
            "beats_naive": (mape < naive) if (mape is not None and naive is not None)
                           else None,
            "pairs": sorted({f"{r['dev']}->{r['prod']}" for r in rows}),
        }

    def observations_for_calibration(self, dev, prod):
        """
        Resolved rows as (predicted_scaling, actual_scaling), ready for CalibrationStore.

        Only rows that carry dev_ms qualify: a scaling factor needs both sides, and a
        prediction logged without its dev-side runtime cannot produce one.
        """
        out = []
        for r in self.resolved():
            if r["dev"] != dev or r["prod"] != prod or not r.get("dev_ms"):
                continue
            p = r.get("scaling") or (r["predicted_ms"] / r["dev_ms"])
            a = r["actual_ms"] / r["dev_ms"]
            if p > 0 and a > 0:
                out.append((p, a, str(r["query_id"])))
        return out


__all__ = ["Telemetry", "DEFAULT_PATH"]

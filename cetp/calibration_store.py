"""
Per-pair calibration state: the mechanism by which the system improves with use.

WHY THIS EXISTS. Measured on real hardware, an uncalibrated prediction across an
architecture boundary is *worse than assuming production equals development* -- 72.84%
MAPE against 21.41% naive on an x86 machine predicted from an arm64 laptop. Five queries
measured on that machine take it to 23.38%. Calibration is therefore not an optimisation,
it is the difference between a usable prediction and a harmful one, and it has to
accumulate somewhere rather than be re-derived by hand.

WHAT IT STORES. For each ordered (dev, prod) machine pair, the observations
(predicted_scaling, actual_scaling) that have been seen, and the correction derived from
them. Nothing else -- no query text, no literals, no plans. A query id is kept only so an
observation can be superseded when the same query is re-measured.

HOW THE CORRECTION IS DERIVED. The median of actual/predicted, not the mean. The cold-start
study found a single arbitrary observation made the predictor WORSE (19.51% against 15.84%
uncalibrated) because one bad ratio drags a mean. The median is what makes small k safe.

WHAT IT HONESTLY DOES AND DOES NOT PROMISE. Accuracy improves with the number of
observations *for that pair*, and the measured curve is in results/laptop_to_prod.md and
results/cross_isa_prediction.md: k=0 -> k=5 took one cross-ISA pair from 72.84% to 23.38%.
It does NOT improve with the number of machines in the registry: a correction learned for
(laptop, machine-A) says nothing about (laptop, machine-B), because it absorbs that
machine's specific scale error. Claiming otherwise would require pooling across machines,
which this project has not demonstrated and which `pooled_prior` below only approximates.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from .operator_model import fit_calibration

# Below this many observations a pair's correction is reported as provisional. k=1 was
# measured worse than not calibrating at all (33.74% against 29.31% on the container grid,
# and 19.51% against 15.84% in the cold-start study), and k>=3 is where it became reliably
# better on every population tested.
MIN_OBSERVATIONS = 3

DEFAULT_PATH = Path(
    os.environ.get("CETP_CALIBRATION_PATH",
                   Path.home() / ".cetp" / "calibration.json"))


def _key(dev, prod):
    return f"{dev}->{prod}"


class CalibrationStore:
    """
    Append-only-per-query calibration state, keyed by (dev, prod).

    Multi-tenancy is by FILE, not by a tenant column: point CETP_CALIBRATION_PATH at a
    path inside the repository or service that owns the data. Two projects sharing one
    store would pool corrections for environments that are only incidentally named the
    same, which is exactly the kind of silent cross-contamination this project has been
    bitten by elsewhere.
    """

    def __init__(self, path=None):
        self.path = Path(path) if path else DEFAULT_PATH
        self._data = {"version": 1, "pairs": {}}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text())
                if isinstance(loaded, dict) and "pairs" in loaded:
                    self._data = loaded
            except (json.JSONDecodeError, OSError):
                # A corrupt store must not break prediction; it degrades to zero-shot,
                # which the caller is already warned about.
                pass

    # ------------------------------------------------------------------ writing

    def record(self, dev, prod, query_id, predicted_scaling, actual_scaling,
               dev_arch=None, prod_arch=None):
        """Record one measured outcome. Re-measuring a query supersedes its old value."""
        if not (predicted_scaling and predicted_scaling > 0):
            raise ValueError("predicted_scaling must be positive")
        if not (actual_scaling and actual_scaling > 0):
            raise ValueError("actual_scaling must be positive")
        k = _key(dev, prod)
        pair = self._data["pairs"].setdefault(
            k, {"dev": dev, "prod": prod, "observations": {}})
        if dev_arch:
            pair["dev_arch"] = dev_arch
        if prod_arch:
            pair["prod_arch"] = prod_arch
        pair["observations"][str(query_id)] = {
            "predicted_scaling": float(predicted_scaling),
            "actual_scaling": float(actual_scaling),
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        return self

    def save(self):
        """Atomic write, so a crash cannot leave a half-written store."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(self._data, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return self.path

    # ------------------------------------------------------------------ reading

    def observations(self, dev, prod):
        pair = self._data["pairs"].get(_key(dev, prod))
        if not pair:
            return []
        return [(o["predicted_scaling"], o["actual_scaling"])
                for o in pair["observations"].values()]

    def calibration(self, dev, prod):
        """
        Returns {factor, n, provisional, source}.

        `factor` is 1.0 when nothing is known, so an uncalibrated call is a no-op rather
        than an error. `provisional` is True below MIN_OBSERVATIONS, because a correction
        from one or two queries was measured to be worse than none.
        """
        obs = self.observations(dev, prod)
        if not obs:
            return {"factor": 1.0, "n": 0, "provisional": True, "source": "none"}
        return {"factor": float(fit_calibration(obs)), "n": len(obs),
                "provisional": len(obs) < MIN_OBSERVATIONS, "source": "pair"}

    def pairs(self):
        out = []
        for k, p in sorted(self._data["pairs"].items()):
            c = self.calibration(p["dev"], p["prod"])
            out.append({"pair": k, "dev": p["dev"], "prod": p["prod"],
                        "dev_arch": p.get("dev_arch"), "prod_arch": p.get("prod_arch"),
                        **c})
        return out

    def learning_curve(self, dev, prod):
        """
        How the correction has moved as observations accumulated, for this pair.

        This is the auditable version of "the system improves as it sees more": it shows
        the correction converging (or not) rather than asserting that it does.
        """
        pair = self._data["pairs"].get(_key(dev, prod))
        if not pair:
            return []
        obs = sorted(pair["observations"].items(),
                     key=lambda kv: kv[1]["recorded_at"])
        curve, acc = [], []
        for qid, o in obs:
            acc.append((o["predicted_scaling"], o["actual_scaling"]))
            curve.append({"n": len(acc), "query_id": qid,
                          "factor": float(fit_calibration(acc)),
                          "provisional": len(acc) < MIN_OBSERVATIONS})
        return curve

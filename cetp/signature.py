"""
Hardware signatures and the registry (Tier 4).

A signature is what the predictor knows about a machine. The project originally used two
scalars -- STREAM bandwidth and multi-core compute -- and the probe that produced the
bandwidth half was LOST, which meant no new machine could ever be onboarded. That is fixed
by `scripts/collection/hw_probe_suite.c` plus `models/hardware_signatures.json`, and this
module is how the predictor reads them.

Two caveats that travel with every signature, both measured rather than asserted:

  - The two legacy axes correlate at **r = +0.958** across the five EC2 machines (97.9% of
    variance on one principal component), so the signature is effectively ONE-dimensional.
    See results/tier1_hw_signature.md. Treat predictions as driven by one capability axis.
  - The compute axis must be probed at the ACHIEVABLE degree of parallelism
    (1 + max_parallel_workers_per_gather), not at `nproc`. Probing at nproc overstates
    capacity nothing can use: on an 8-core laptop it cost 324% vs 153% MAPE
    (results/tier1_laptop_dev.md). `dop_mismatch` flags machines where this applies.
"""

import json
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
REGISTRY = PROJECT_DIR / "models" / "hardware_signatures.json"

# The five EC2 machines, measured August 2026. Bandwidth is from a STREAM-triad probe whose
# SOURCE NO LONGER EXISTS, so these numbers are frozen and unreproducible -- new machines
# cannot be placed on this axis. Compute is multi-core aggregate GFLOPS at `nproc`, which is
# the signal the headline result (21.61% vs 23.05% MAPE) requires.
LEGACY = {
    "c5a": {"bandwidth": 14.9, "compute": 40.8071, "nproc": 4},
    "m5a": {"bandwidth": 10.01, "compute": 31.5366, "nproc": 4},
    "r5n": {"bandwidth": 9.39, "compute": 24.9681, "nproc": 2},
    "z1d": {"bandwidth": 9.77, "compute": 28.0804, "nproc": 2},
    "c7i": {"bandwidth": 8.44, "compute": 25.8909, "nproc": 2},
}
LEGACY_NOTE = ("compute = multi-core aggregate GFLOPS probed at nproc; bandwidth = "
               "STREAM triad, probe source lost and unreproducible")
PG_DEFAULT_DOP = 3  # 1 + max_parallel_workers_per_gather at PostgreSQL defaults


def load_registry(path=REGISTRY):
    if not Path(path).exists():
        return {"schema": "cetp-hardware-signature/2", "machines": {}}
    return json.loads(Path(path).read_text())


def legacy_signature(machine_id):
    """The frozen two-scalar signature for one of the five original EC2 machines."""
    if machine_id not in LEGACY:
        return None
    s = dict(LEGACY[machine_id])
    s["source"] = "legacy_frozen"
    s["note"] = LEGACY_NOTE
    # nproc > achievable DOP means the compute figure describes unreachable capacity.
    s["dop_mismatch"] = s["nproc"] > PG_DEFAULT_DOP
    return s


def registry_signature(machine_id, path=REGISTRY):
    """A signature measured by hw_probe_suite and recorded in the registry."""
    reg = load_registry(path)
    e = reg.get("machines", {}).get(machine_id)
    if not e:
        return None
    probe, pg, host = e.get("probe", {}), e.get("postgres", {}), e.get("host", {})
    dop = pg.get("achievable_dop") or PG_DEFAULT_DOP
    nproc = host.get("nproc")
    # Prefer the DOP-matched compute figure; it is the one a cost model should use.
    compute = probe.get("fp_vector_gflops_dop") or probe.get("fp_vector_gflops_nproc")
    return {
        "bandwidth": probe.get("mem_bandwidth_gbs_mt"),
        "compute": compute,
        "compute_at_nproc": probe.get("fp_vector_gflops_nproc"),
        "achievable_dop": dop, "nproc": nproc,
        "mem_latency_ns": probe.get("mem_latency_ns"),
        "source": "registry_probe_v2",
        "dop_mismatch": False,  # by construction: compute is measured at the DOP
        "validated": e.get("validation", {}).get("ok", False),
        "probed_at": e.get("probed_at"),
        "note": "hw_probe_suite v2; compute probed at the achievable DOP",
    }


def get_signature(machine_id, path=REGISTRY, prefer="registry"):
    """Resolve a machine to a signature, registry first.

    **Registry and legacy signatures are NOT on the same scale.** Registry bandwidth comes
    from a different STREAM implementation than the lost original, and registry compute is
    probed at the DOP rather than at nproc. Mixing them in one ratio is meaningless, so
    `comparable_with` is returned and callers must check it.
    """
    order = ([registry_signature, legacy_signature] if prefer == "registry"
             else [legacy_signature, registry_signature])
    for fn in order:
        s = fn(machine_id, path) if fn is registry_signature else fn(machine_id)
        if s and s.get("bandwidth") and s.get("compute"):
            s["machine_id"] = machine_id
            s["comparable_with"] = s["source"]
            return s
    return None


def ratios(dev, prod):
    """Capability ratios prod/dev. Refuses to mix signature scales."""
    if dev.get("comparable_with") != prod.get("comparable_with"):
        raise ValueError(
            f"cannot compare a {dev.get('source')} signature with a {prod.get('source')} "
            f"one: bandwidth comes from different STREAM implementations and compute is "
            f"probed at different thread counts. Re-probe both machines with "
            f"scripts/collection/hw_signature.py, or use two legacy machines.")
    return {
        "bandwidth_ratio": prod["bandwidth"] / dev["bandwidth"],
        "compute_ratio": prod["compute"] / dev["compute"],
    }


def warnings_for(dev, prod):
    out = []
    for role, s in (("dev", dev), ("prod", prod)):
        if s.get("dop_mismatch"):
            out.append(
                f"{role} machine {s['machine_id']} has nproc={s.get('nproc')} > achievable "
                f"DOP {PG_DEFAULT_DOP}, so its compute figure describes capacity no query "
                f"can use (measured cost: 324% vs 153% MAPE). Re-probe it with "
                f"scripts/collection/hw_signature.py.")
        if s.get("source") == "legacy_frozen":
            out.append(f"{role} machine {s['machine_id']} uses the frozen legacy signature; "
                       f"its bandwidth probe no longer exists and cannot be reproduced.")
        if s.get("source") == "registry_probe_v2" and not s.get("validated", True):
            out.append(f"{role} machine {s['machine_id']} failed probe validation; "
                       f"do not trust its signature.")
    out.append("The two signature axes correlate at r=+0.958 on the reference machines, so "
               "treat this as one capability axis, not two (results/tier1_hw_signature.md).")
    return out


def list_machines(path=REGISTRY):
    reg = load_registry(path)
    return {"registry": sorted(reg.get("machines", {})), "legacy": sorted(LEGACY)}

"""
Per-node decomposition of an EXPLAIN ANALYZE plan, and the node -> operator mapping.

WHY THIS IS NOT TRIVIAL. PostgreSQL's `Actual Total Time` is two things at once that both
have to be undone before the numbers mean what they look like:

  1. It is CUMULATIVE -- it includes all descendants. A node's own cost is its inclusive
     time minus the inclusive time of its children.
  2. It is PER LOOP -- an inner node of a nested loop reports the AVERAGE time of one
     loop, not the total. The total is `Actual Total Time * Actual Loops`.

Getting either wrong silently inflates or deflates the decomposition, and the error is
largest exactly where it matters most (nested loops, correlated subplans). The invariant
is checked in `decompose()` and reported, not assumed.

PARALLELISM CAVEAT. Under a Gather, `Actual Total Time` is averaged across the leader and
its workers, so self-time for a node below a Gather is "average per participant", not wall
time. Summing it therefore does NOT reproduce wall-clock execution time when the plan is
parallel; it reproduces the total CPU-time profile. For a ratio between two machines
measured the same way this is the right quantity, but it is not a wall-clock budget, and
`decompose()` reports `parallel` so a caller can refuse those plans.
"""

from __future__ import annotations

# node type -> the operator_probe axis that governs its cost.
#
# Mapping decisions worth arguing with:
#   Index/Bitmap scans -> nestloop, because the probe's nestloop axis IS an index-driven
#   lookup loop, so it measures random access + TLB behaviour, which is what these pay.
#   Merge Join -> sort, because a merge join's cost is dominated by ordered streaming and
#   the sorts feeding it, not by hashing.
#   Hash -> hashjoin, because the build side is part of the same hash machinery.
OPERATOR_MAP = {
    "Seq Scan": "seqscan",
    "Hash Join": "hashjoin",
    "Hash": "hashjoin",
    "Sort": "sort",
    "Incremental Sort": "sort",
    "Merge Join": "sort",
    "Nested Loop": "nestloop",
    "Index Scan": "nestloop",
    "Index Only Scan": "nestloop",
    "Bitmap Heap Scan": "nestloop",
    "Bitmap Index Scan": "nestloop",
}

# Aggregate is split by strategy: hashed aggregation is the hashagg axis, sorted/plain
# aggregation is a streaming pass and behaves like a scan.
AGG_STRATEGY = {"Hashed": "hashagg", "Sorted": "sort", "Plain": "seqscan", "Mixed": "hashagg"}

# Nodes that are control flow or buffering rather than work. They get no operator and
# their self-time lands in the unmapped residual, which is reported.
PASSTHROUGH = {
    "Gather", "Gather Merge", "Limit", "Result", "Append", "Merge Append",
    "Materialize", "Memoize", "Subquery Scan", "SetOp", "Unique", "Group",
    "LockRows", "ProjectSet", "Recursive Union", "WindowAgg", "CTE Scan",
}

# How a node's cost responds to processing r times as many rows. Used only when dev and
# prod see different data volumes; with identical data every factor is 1.0.
COMPLEXITY = {
    "seqscan": "linear",
    "hashjoin": "linear",
    "hashagg": "linear",
    "nestloop": "linear",
    "sort": "nlogn",
}


def operator_for(node) -> str | None:
    """The probe axis this node's self-time should be scaled by, or None if unmapped."""
    nt = node.get("Node Type", "")
    if nt == "Aggregate":
        return AGG_STRATEGY.get(node.get("Strategy", "Plain"), "hashagg")
    if nt in PASSTHROUGH:
        return None
    return OPERATOR_MAP.get(nt)


def _unwrap(plan):
    """Accept the many shapes EXPLAIN FORMAT JSON comes back in."""
    if isinstance(plan, list):
        if not plan:
            raise ValueError("empty plan")
        plan = plan[0]
    if isinstance(plan, dict) and "Plan" in plan:
        return plan
    return {"Plan": plan}


def decompose(plan):
    """
    Flatten a plan into nodes carrying SELF time, with the cumulative and per-loop
    corrections applied.

    Returns {"nodes": [...], "execution_time_ms": float|None, "parallel": bool,
             "self_time_sum_ms": float, "residual_ms": float, "analyzed": bool}

    Each node: {node_type, operator, self_ms, inclusive_ms, rows, loops, depth, parallel_aware}
    """
    root = _unwrap(plan)
    top = root["Plan"]
    nodes = []
    parallel = False

    def inclusive_ms(n):
        """Total time this subtree cost, undoing the per-loop averaging."""
        att = n.get("Actual Total Time")
        if att is None:
            return None
        return float(att) * float(n.get("Actual Loops", 1) or 1)

    def walk(n, depth, under_gather=False):
        nonlocal parallel
        if n.get("Parallel Aware") or n.get("Node Type") in ("Gather", "Gather Merge"):
            parallel = True
        inc = inclusive_ms(n)
        children = n.get("Plans", []) or []
        child_inc = 0.0
        for c in children:
            ci = inclusive_ms(c)
            if ci is not None:
                child_inc += ci
        self_ms = None if inc is None else inc - child_inc
        # Clamp: subtraction of averaged parallel children can go slightly negative.
        if self_ms is not None and self_ms < 0:
            self_ms = 0.0
        nodes.append({
            "node_type": n.get("Node Type", "?"),
            "operator": operator_for(n),
            "self_ms": self_ms,
            "inclusive_ms": inc,
            "rows": float(n.get("Actual Rows", 0) or 0) * float(n.get("Actual Loops", 1) or 1),
            "plan_rows": float(n.get("Plan Rows", 0) or 0),
            "loops": float(n.get("Actual Loops", 1) or 1),
            "depth": depth,
            "parallel_aware": bool(n.get("Parallel Aware")),
            # THE criterion that matters for timing, and it is not `Parallel Aware`.
            # `Parallel Aware` marks only the nodes that PARTITION work (a Parallel Seq
            # Scan). Every node BELOW a Gather is executed once per participant and has
            # its Actual Total Time averaged across them -- measured on TPC-H q19, the
            # Nested Loop, inner Aggregate and Index Scan all report loops=3 while only
            # the Seq Scan is Parallel Aware. Keying the parallelism correction on
            # `Parallel Aware` therefore corrected 46% of the plan's time instead of 100%
            # and predicted 1.49x where the truth was 3.01x.
            "under_gather": under_gather,
        })
        gather_below = under_gather or n.get("Node Type") in ("Gather", "Gather Merge")
        for c in children:
            walk(c, depth + 1, gather_below)

    walk(top, 0, False)
    analyzed = all(n["self_ms"] is not None for n in nodes) and bool(nodes)
    self_sum = sum(n["self_ms"] or 0.0 for n in nodes)
    exec_ms = root.get("Execution Time")
    exec_ms = float(exec_ms) if exec_ms is not None else None

    return {
        "nodes": nodes,
        "execution_time_ms": exec_ms,
        "parallel": parallel,
        "analyzed": analyzed,
        "self_time_sum_ms": self_sum,
        # How much of reported execution time the node self-times fail to account for.
        # Large residual => the decomposition is not trustworthy for this plan.
        "residual_ms": (exec_ms - self_sum) if exec_ms is not None else None,
        "residual_frac": ((exec_ms - self_sum) / exec_ms)
                         if (exec_ms not in (None, 0)) else None,
    }


def operator_profile(plan):
    """self-time in ms per operator axis, plus the unmapped residual."""
    d = decompose(plan)
    prof = {}
    unmapped = 0.0
    for n in d["nodes"]:
        t = n["self_ms"] or 0.0
        if n["operator"] is None:
            unmapped += t
        else:
            prof[n["operator"]] = prof.get(n["operator"], 0.0) + t
    return {"profile": prof, "unmapped_ms": unmapped,
            "unmapped_frac": unmapped / d["self_time_sum_ms"] if d["self_time_sum_ms"] else 0.0,
            "decomposition": d}

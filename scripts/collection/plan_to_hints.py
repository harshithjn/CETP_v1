"""
Convert an EXPLAIN plan into pg_hint_plan hints, so a specific plan can be FORCED
(CONFERENCE_PLAN.md item 3).

Tier 3's plan-drift decomposition was observational: it compared environment pairs that
happened to agree on a plan against pairs that did not. That cannot separate "the plan
changed" from "the settings also changed execution", because the settings that change the
plan (work_mem, max_parallel_workers_per_gather) change execution too.

Forcing makes it causal. For query q and environments A and B:

    total    = T(B, P_B) / T(A, P_A)      what you observe across environments
    plan     = T(B, P_B) / T(B, P_A)      B's settings, A's plan vs B's plan -> PURE plan effect
    settings = T(B, P_A) / T(A, P_A)      A's plan throughout -> PURE settings effect

and total = plan x settings exactly.

Hints emitted:
  - scan method per relation      SeqScan(a) / IndexScan(a idx) / IndexOnlyScan / BitmapScan
  - join method per join node     HashJoin(a b) / MergeJoin(...) / NestLoop(...)
  - join order                    Leading((a b) c)
  - parallel degree per relation  Parallel(a N hard)

**Forcing is never assumed to have worked.** The caller must re-plan with the hints and check
that the resulting plan signature equals the target. pg_hint_plan silently ignores hints it
cannot apply, so an unverified "forced" plan is worthless. See force_and_verify().
"""

import hashlib
import json
import subprocess

SCAN_HINT = {
    "Seq Scan": "SeqScan",
    "Index Scan": "IndexScan",
    "Index Only Scan": "IndexOnlyScan",
    "Bitmap Heap Scan": "BitmapScan",
    "Tid Scan": "TidScan",
}
JOIN_HINT = {"Hash Join": "HashJoin", "Merge Join": "MergeJoin", "Nested Loop": "NestLoop"}


def plan_signature(node):
    """Structural fingerprint: operators, join order, parallelism. Ignores timings."""
    parts = [node.get("Node Type", ""), str(node.get("Parallel Aware", False)),
             str(node.get("Workers Planned", 0) or 0),
             node.get("Relation Name", "") or "", node.get("Join Type", "") or "",
             node.get("Index Name", "") or "", node.get("Strategy", "") or ""]
    for c in node.get("Plans", []):
        parts.append(plan_signature(c))
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def _rel(node):
    """pg_hint_plan addresses relations by alias when one exists, else by name."""
    return node.get("Alias") or node.get("Relation Name")


def _collect(node, scans, joins, parallel):
    """Returns the ordered list of relation identifiers beneath this node."""
    nt = node.get("Node Type", "")
    rel = _rel(node)

    if nt in SCAN_HINT and rel:
        idx = node.get("Index Name")
        if nt in ("Index Scan", "Index Only Scan") and idx:
            scans.append(f"{SCAN_HINT[nt]}({rel} {idx})")
        else:
            scans.append(f"{SCAN_HINT[nt]}({rel})")
        if node.get("Parallel Aware") or (node.get("Workers Planned") or 0) > 0:
            # Workers Planned lives on the Gather, so this is filled in by the caller pass.
            parallel.setdefault(rel, 0)
        return [rel]

    if nt in ("Gather", "Gather Merge"):
        w = node.get("Workers Planned", 0) or 0
        below = []
        for c in node.get("Plans", []):
            below += _collect(c, scans, joins, parallel)
        for r in below:
            if r in parallel:
                parallel[r] = max(parallel[r], w)
        return below

    below = []
    for c in node.get("Plans", []):
        below += _collect(c, scans, joins, parallel)

    if nt in JOIN_HINT and len(below) >= 2:
        joins.append(f"{JOIN_HINT[nt]}({' '.join(below)})")
    return below


def _leading(node):
    """Build a Leading(...) expression mirroring the plan's join tree."""
    nt = node.get("Node Type", "")
    rel = _rel(node)
    if nt in SCAN_HINT and rel:
        return rel
    kids = [_leading(c) for c in node.get("Plans", [])]
    kids = [k for k in kids if k]
    if nt in JOIN_HINT and len(kids) >= 2:
        return "(" + " ".join(kids) + ")"
    if len(kids) == 1:
        return kids[0]
    if len(kids) > 1:
        return "(" + " ".join(kids) + ")"
    return None


def plan_to_hints(plan_root, include_leading=True, include_parallel=True):
    scans, joins, parallel = [], [], {}
    _collect(plan_root, scans, joins, parallel)
    hints = list(dict.fromkeys(scans)) + list(dict.fromkeys(joins))
    if include_parallel:
        for rel, w in sorted(parallel.items()):
            # `hard` forces the degree even when the planner would not choose it.
            hints.append(f"Parallel({rel} {w} hard)")
    if include_leading:
        lead = _leading(plan_root)
        if lead and lead.startswith("("):
            hints.append(f"Leading({lead})")
    return "/*+ " + " ".join(hints) + " */" if hints else ""


def enable_hints(cur):
    """Arm pg_hint_plan in this session.

    `CREATE EXTENSION pg_hint_plan` only registers the SQL objects -- the planner hook lives
    in the shared library, which must be LOADED into the backend before any hint has an
    effect. Without this, hints are silently ignored: the first version of this experiment
    reported a 1.54x "plan effect" that was pure measurement noise, because nothing was ever
    actually forced.
    """
    cur.execute("LOAD 'pg_hint_plan'")
    cur.execute("set pg_hint_plan.enable_hint = on")


def run_explain(cur, sql, settings=None):
    for k, v in (settings or {}).items():
        cur.execute(f"set {k} = '{v}'")
    cur.execute(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}")
    res = cur.fetchone()[0][0]
    return res


def force_and_verify(cur, sql, target_plan, settings=None):
    """Run `sql` under `settings` while forcing `target_plan`'s shape.

    Returns (result, hints, verified). **verified is False whenever the achieved plan
    signature differs from the target** -- pg_hint_plan ignores hints it cannot apply, so
    an unverified result must be discarded rather than reported as a forced plan.
    """
    hints = plan_to_hints(target_plan)
    if not hints:
        return None, "", False
    for k, v in (settings or {}).items():
        cur.execute(f"set {k} = '{v}'")
    enable_hints(cur)
    # Keep hint parse failures visible in the log rather than silent.
    cur.execute("set pg_hint_plan.message_level = 'notice'")
    # The hint comment MUST be the first thing in the statement. Placing it after EXPLAIN
    # (as the first version did) means pg_hint_plan never sees it.
    cur.execute(f"{hints}\nEXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}")
    res = cur.fetchone()[0][0]
    achieved = plan_signature(res["Plan"])
    return res, hints, achieved == plan_signature(target_plan)


if __name__ == "__main__":
    import sys
    import psycopg2
    db = sys.argv[1] if len(sys.argv) > 1 else "tpch"
    qfile = sys.argv[2] if len(sys.argv) > 2 else "scripts/collection/queries/q5.sql"
    sql = "\n".join(l for l in open(qfile).read().splitlines()
                    if not l.strip().startswith("--")).strip()
    conn = psycopg2.connect(dbname=db)
    conn.autocommit = True
    cur = conn.cursor()
    enable_hints(cur)
    base = run_explain(cur, sql, {"max_parallel_workers_per_gather": 2, "work_mem": "4MB"})
    print("hints:", plan_to_hints(base["Plan"])[:400])
    res, hints, ok = force_and_verify(
        cur, sql, base["Plan"],
        {"max_parallel_workers_per_gather": 8, "work_mem": "64MB"})
    print(f"forced under different settings, signature matched: {ok}")
    print(f"  original {base['Execution Time']:.0f} ms -> forced {res['Execution Time']:.0f} ms")

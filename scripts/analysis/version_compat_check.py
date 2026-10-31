"""
Does the plan parser actually work across PostgreSQL versions?

The project claims to read `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`, but that format is not
frozen: PostgreSQL adds node fields between releases, and a parser written against one
release can silently mis-handle another. Rather than assert compatibility, this runs the
whole parsing stack over every stored plan from every version collected and reports what
happens.

Checked per plan, for each version:
  decompose()          every node gets a self time, with the cumulative and per-loop
                       corrections applied
  operator_profile()   how much of the plan's time maps to a known operator axis; an
                       unmapped share is not an error but IS a loss of resolution, and a
                       new node type in a new release shows up here first
  redact_plan()        plus assert_clean(), because a field added in a new release may
                       carry literals that the redactor has never seen

Versions actually available here are 14, 15, 17 and 18. **13 and 16 are not tested and must
not be claimed.**
"""

import gzip
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR))

from cetp.plan_nodes import decompose, operator_for, operator_profile   # noqa: E402
from cetp.redact import assert_clean, redact_plan                       # noqa: E402

RESULTS = PROJECT_DIR / "results"


def load(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as fh:
        return json.load(fh)


def node_keys(plan):
    out = Counter()
    root = plan["Plan"] if isinstance(plan, dict) and "Plan" in plan else plan

    def walk(n):
        for k in n:
            if k != "Plans":
                out[k] += 1
        for c in n.get("Plans", []) or []:
            walk(c)
    walk(root)
    return out


def node_types(plan):
    out = Counter()
    root = plan["Plan"] if isinstance(plan, dict) and "Plan" in plan else plan

    def walk(n):
        out[n.get("Node Type", "?")] += 1
        for c in n.get("Plans", []) or []:
            walk(c)
    walk(root)
    return out


def check_version(version, plan_dir):
    files = sorted(plan_dir.rglob("*.json*"))
    rec = {"version": version, "n_plans": 0, "decompose_ok": 0, "redact_ok": 0,
           "errors": [], "unmapped_node_types": Counter(), "node_keys": Counter(),
           "max_unmapped_frac": 0.0, "mean_unmapped_frac": 0.0,
           "max_abs_residual_frac": 0.0}
    unmapped = []
    for f in files:
        try:
            plan = load(f)
        except Exception as exc:                                  # noqa: BLE001
            rec["errors"].append(f"{f.name}: unreadable: {exc}")
            continue
        rec["n_plans"] += 1
        rec["node_keys"].update(node_keys(plan))

        try:
            d = decompose(plan)
            assert d["nodes"], "no nodes"
            assert all(n["self_ms"] is not None for n in d["nodes"]), "a node has no time"
            rec["decompose_ok"] += 1
            if d.get("residual_frac") is not None:
                rec["max_abs_residual_frac"] = max(rec["max_abs_residual_frac"],
                                                   abs(d["residual_frac"]))
        except Exception as exc:                                  # noqa: BLE001
            rec["errors"].append(f"{f.name}: decompose: {type(exc).__name__}: {exc}")
            continue

        prof = operator_profile(plan)
        unmapped.append(prof["unmapped_frac"])
        rec["max_unmapped_frac"] = max(rec["max_unmapped_frac"], prof["unmapped_frac"])
        for nt, c in node_types(plan).items():
            if operator_for({"Node Type": nt}) is None and nt not in (
                    "Gather", "Gather Merge", "Limit", "Result", "Append", "Merge Append",
                    "Materialize", "Memoize", "Subquery Scan", "SetOp", "Unique", "Group",
                    "LockRows", "ProjectSet", "Recursive Union", "WindowAgg", "CTE Scan",
                    "Aggregate"):
                rec["unmapped_node_types"][nt] += c

        try:
            assert_clean(redact_plan(plan))
            rec["redact_ok"] += 1
        except Exception as exc:                                  # noqa: BLE001
            rec["errors"].append(f"{f.name}: redact: {type(exc).__name__}: {str(exc)[:120]}")

    rec["mean_unmapped_frac"] = (sum(unmapped) / len(unmapped)) if unmapped else 0.0
    return rec


def main():
    dirs = sorted(PROJECT_DIR.glob("data/plans_pg*"))
    if not dirs:
        sys.exit("no data/plans_pg* directories found")
    recs = []
    for d in dirs:
        v = d.name.replace("plans_pg", "")
        recs.append(check_version(v, d))

    print(f"{'pg':>4s} {'plans':>6s} {'decompose':>10s} {'redact':>8s} "
          f"{'unmapped(mean)':>15s} {'unmapped(max)':>14s} {'errors':>7s}")
    ok = True
    for r in recs:
        bad = len(r["errors"])
        ok &= (bad == 0 and r["decompose_ok"] == r["n_plans"]
               and r["redact_ok"] == r["n_plans"])
        print(f"{r['version']:>4s} {r['n_plans']:6d} "
              f"{r['decompose_ok']:>4d}/{r['n_plans']:<5d} "
              f"{r['redact_ok']:>3d}/{r['n_plans']:<4d} "
              f"{r['mean_unmapped_frac']:14.1%} {r['max_unmapped_frac']:13.1%} {bad:7d}")

    versions = [r["version"] for r in recs]
    allk = set().union(*[set(r["node_keys"]) for r in recs])
    differing = [k for k in sorted(allk)
                 if len({k in r["node_keys"] for r in recs}) > 1]
    print(f"\nnode fields that differ across {', '.join('pg'+v for v in versions)}:")
    for k in differing:
        present = " ".join(f"pg{r['version']}={'Y' if k in r['node_keys'] else '-'}"
                           for r in recs)
        print(f"  {k:24s} {present}")
    if not differing:
        print("  (none)")

    unmapped_types = Counter()
    for r in recs:
        unmapped_types.update(r["unmapped_node_types"])
    if unmapped_types:
        print("\nnode types with no operator axis (resolution lost, not an error):")
        for nt, c in unmapped_types.most_common():
            print(f"  {nt:30s} x{c}")

    for r in recs:
        for e in r["errors"][:5]:
            print(f"\nERROR pg{r['version']}: {e}")

    payload = {
        "versions_tested": versions,
        "versions_NOT_tested": ["13", "16"],
        "per_version": [{k: (dict(v) if isinstance(v, Counter) else v)
                         for k, v in r.items()} for r in recs],
        "differing_node_fields": differing,
        "all_versions_parse_cleanly": ok,
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "version_compat.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"\nall versions parse cleanly: {ok}")
    print(f"wrote {RESULTS / 'version_compat.json'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

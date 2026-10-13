"""
Plan redaction (Tier 4).

An `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` plan embeds the query's literal values:

    "Filter": "(l_shipdate <= '1998-09-02'::date)"
    "Index Cond": "(c_custkey = 42)"
    "Filter": "(email = 'someone@example.com'::text)"

On a real database those literals are customer data. Any product that ships a plan off the
user's machine -- to a prediction service, a shared signature registry, or a support ticket --
is exfiltrating PII unless it strips them first. **This must run before a plan leaves the
machine, not after.**

What the predictor actually needs from a plan is its STRUCTURE: node types, join methods,
cardinalities, costs, buffer counts, parallelism. None of that requires literals, so
redaction is close to free in accuracy terms.

Two levels:

  structural  (default) Strips literals from every expression field but keeps relation,
              column and index names. Enough for a plan-structure model. Schema names can
              still be mildly revealing ("patient_ssn"), so this is the right level for a
              first-party service, not for a public dataset.
  full        Additionally replaces every identifier with a stable salted hash, so the same
              column maps to the same token across plans without revealing its name. Enough
              to publish.

Verification, not assumption: `assert_clean()` re-scans a redacted plan for anything that
still looks like a literal and raises if it finds one. Call it in CI.
"""

import hashlib
import json
import re

# Fields PostgreSQL fills with SQL expressions. These are where literals hide.
EXPRESSION_FIELDS = (
    "Filter", "Index Cond", "Recheck Cond", "Hash Cond", "Merge Cond", "Join Filter",
    "TID Cond", "Cache Key", "Group Key", "Sort Key", "Presorted Key", "Output",
    "Hash Key", "One-Time Filter", "Function Call", "Table Function Call",
    "Subplans Removed", "Conflict Filter", "Repeatable Seed", "Params Evaluated",
    "Merge Key", "Order By",
)
IDENTIFIER_FIELDS = (
    "Relation Name", "Alias", "Index Name", "CTE Name", "Schema", "Function Name",
    "Tuplestore Name", "Subplan Name", "Parent Relationship",
)
# Node-level fields that are structure, never data -- always preserved.
_NEVER_TOUCH = {"Node Type", "Strategy", "Join Type", "Scan Direction", "Parallel Aware",
                "Async Capable", "Partial Mode", "Operation", "Command", "Sort Method"}

_STR_LITERAL = re.compile(r"'(?:[^']|'')*'")
_NUM_LITERAL = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?![\w.])")
# $1, $2 ... are parameter placeholders, already safe.
_SAFE_TOKENS = re.compile(r"\$\d+")


def _redact_expression(expr):
    """Replace literal values with type-preserving placeholders.

    Type and shape are preserved because they matter to the planner's selectivity and
    therefore to the model; the value is not.
    """
    if not isinstance(expr, str):
        return expr
    out = _STR_LITERAL.sub("'?'", expr)
    out = _NUM_LITERAL.sub("?", out)
    return out


def _hash_ident(name, salt):
    if not isinstance(name, str) or not name:
        return name
    h = hashlib.blake2s(f"{salt}:{name}".encode(), digest_size=5).hexdigest()
    return f"id_{h}"


def redact_plan(obj, level="structural", salt="cetp"):
    """Redact a parsed EXPLAIN FORMAT JSON structure in place-safe fashion.

    Accepts the whole EXPLAIN output (a list), a single plan wrapper (a dict with "Plan"),
    or a bare plan node.
    """
    if isinstance(obj, list):
        return [redact_plan(o, level, salt) for o in obj]
    if not isinstance(obj, dict):
        return obj

    out = {}
    for k, v in obj.items():
        if k in _NEVER_TOUCH:
            out[k] = v
        elif k in EXPRESSION_FIELDS:
            if isinstance(v, list):
                out[k] = [_redact_expression(x) for x in v]
            else:
                out[k] = _redact_expression(v)
        elif k in IDENTIFIER_FIELDS and level == "full":
            out[k] = _hash_ident(v, salt)
        elif isinstance(v, (dict, list)):
            out[k] = redact_plan(v, level, salt)
        else:
            out[k] = v

    # Identifiers survive inside expressions too, so hash them there as well at full level.
    if level == "full":
        for k in EXPRESSION_FIELDS:
            if k in out and isinstance(out[k], str):
                out[k] = re.sub(r"\b[a-z_][a-z0-9_]{2,}\b",
                                lambda m: _hash_ident(m.group(0), salt), out[k])
            elif k in out and isinstance(out[k], list):
                out[k] = [re.sub(r"\b[a-z_][a-z0-9_]{2,}\b",
                                 lambda m: _hash_ident(m.group(0), salt), x)
                          if isinstance(x, str) else x for x in out[k]]
    return out


def _walk_strings(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_strings(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_strings(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        yield path, obj


def assert_clean(plan, level="structural"):
    """Re-scan a redacted plan and raise if anything still looks like a literal.

    Redaction that is not verified is a promise, not a control. Call this in CI.
    """
    problems = []
    for path, s in _walk_strings(plan):
        field = path.rsplit(".", 1)[-1].split("[")[0]
        if field in _NEVER_TOUCH or field == "Sort Method":
            continue
        probe = _SAFE_TOKENS.sub("", s)
        if _STR_LITERAL.search(probe.replace("'?'", "")):
            problems.append(f"{path}: string literal survives -> {s[:90]}")
        # Numbers are legitimate in numeric *metric* fields; only expressions are checked.
        if field in EXPRESSION_FIELDS and _NUM_LITERAL.search(probe.replace("?", "")):
            problems.append(f"{path}: numeric literal survives -> {s[:90]}")
    if problems:
        raise ValueError("redaction incomplete:\n  " + "\n  ".join(problems[:12]))
    return True


def redact_file(path_in, path_out, level="structural", salt="cetp"):
    with open(path_in) as fh:
        plan = json.load(fh)
    out = redact_plan(plan, level, salt)
    assert_clean(out, level)
    with open(path_out, "w") as fh:
        json.dump(out, fh, indent=2)
    return out


def main(argv=None):
    import argparse
    import sys
    ap = argparse.ArgumentParser(
        prog="cetp-redact",
        description="Strip literal values from an EXPLAIN FORMAT JSON plan before it leaves "
                    "the machine.")
    ap.add_argument("plan", help="path to EXPLAIN FORMAT JSON, or - for stdin")
    ap.add_argument("-o", "--out", default="-", help="output path, or - for stdout")
    ap.add_argument("--level", choices=["structural", "full"], default="structural")
    ap.add_argument("--salt", default="cetp",
                    help="salt for identifier hashing at --level full; keep it secret and "
                         "stable, since the same salt gives the same tokens")
    ap.add_argument("--check", action="store_true",
                    help="only verify that the input is already redacted; exit 1 if not")
    args = ap.parse_args(argv)

    raw = sys.stdin.read() if args.plan == "-" else open(args.plan).read()
    plan = json.loads(raw)
    if args.check:
        try:
            assert_clean(plan, args.level)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        print("plan is clean")
        return 0
    out = redact_plan(plan, args.level, args.salt)
    assert_clean(out, args.level)
    text = json.dumps(out, indent=2)
    if args.out == "-":
        print(text)
    else:
        with open(args.out, "w") as fh:
            fh.write(text + "\n")
        print(f"wrote {args.out} (level={args.level})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

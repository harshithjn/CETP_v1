"""
Where plans come from in production: auto_explain and pg_stat_statements.

WITHOUT THIS THERE IS NO PRODUCT LOOP. Every prediction elsewhere in this project starts
from a plan somebody captured by hand. A developer will not run EXPLAIN ANALYZE on each of
their queries, so the plans have to come from what the server already records.

TWO SOURCES, and they answer different questions.

  auto_explain          writes a real plan, with real timings, for queries that exceeded a
                        duration threshold. This is the only source of ACTUAL per-node
                        timings, which is what the per-operator model needs. It is also the
                        only source that can close the calibration loop, because it carries
                        what the query really cost.

  pg_stat_statements    aggregates (calls, total_exec_time, mean, rows) per normalised
                        query. No plan and no per-node detail, so it cannot drive a
                        prediction -- but it tells you WHICH queries matter, which is how
                        you pick the few to measure under a calibration budget.

Used together: pg_stat_statements ranks, auto_explain supplies the plan for the ones worth
predicting, and the measured duration from auto_explain feeds cetp.calibration_store so the
correction for that environment pair improves as the system runs.

PRIVACY. auto_explain output embeds the query's literal values, which on a real database are
user data. `parse_auto_explain_log` therefore runs every plan through `cetp.redact` before
returning it, and `assert_clean` is applied so a literal that survives raises rather than
leaks. This is not optional and there is no flag to disable it: the plan is redacted before
it is ever handed back to a caller.

SETUP the server needs (documented here because the product is useless without it):

    shared_preload_libraries = 'auto_explain,pg_stat_statements'
    auto_explain.log_min_duration = '200ms'     -- only the slow ones
    auto_explain.log_analyze = on               -- REQUIRED: without it there are no timings
    auto_explain.log_buffers = on
    auto_explain.log_format = 'json'            -- REQUIRED: this parser reads JSON
    auto_explain.log_nested_statements = on
    CREATE EXTENSION pg_stat_statements;
"""

from __future__ import annotations

import json
import re

from .redact import assert_clean, redact_plan

# auto_explain with log_format='json' emits a log line containing "duration: <ms> ms  plan:"
# followed by a JSON object. The JSON may span many lines and be prefixed on each line by
# the log_line_prefix, so the object is located by brace balance rather than by line.
_DURATION = re.compile(r"duration:\s*([0-9.]+)\s*ms\s+plan:", re.I)


# A log_line_prefix is repeated on EVERY line of a multi-line log message, so the prefixes
# land *inside* the JSON object and make it unparseable. Postgres marks continuation lines
# either with one of these severity tags or with a leading tab.
_LOG_TAG = re.compile(r"^.*?\b(?:DETAIL|LOG|STATEMENT|CONTEXT|HINT|WARNING|NOTICE)\s*:\s{0,2}")


def _strip_log_prefixes(block):
    """
    Remove a repeated log_line_prefix from the continuation lines of one log message.

    Only applied to lines that look prefixed: a line whose content starts with JSON
    punctuation is left alone, so unprefixed logs (log_line_prefix = '') still work.
    """
    out = []
    for i, line in enumerate(block.split("\n")):
        if i == 0:
            out.append(line)
            continue
        if line[:1] == "\t":
            out.append(line.lstrip("\t"))
            continue
        stripped = _LOG_TAG.sub("", line, count=1)
        out.append(stripped if stripped != line else line)
    return "\n".join(out)


def _balanced_json(text, start):
    """Extract the JSON object beginning at `start`, tolerating log prefixes."""
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def parse_auto_explain_log(text, level="structural", strict=True):
    """
    Pull every plan out of an auto_explain log.

    Returns a list of {duration_ms, plan, query_id} with every plan ALREADY REDACTED.
    `strict=True` raises if redaction leaves a literal behind; `strict=False` drops that
    entry and records it in the `dropped` list of the companion summary.
    """
    out, dropped = [], []
    for m in _DURATION.finditer(text):
        brace = text.find("{", m.end())
        if brace < 0:
            continue
        raw = _balanced_json(text, brace)
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            # Retry with the repeated log_line_prefix removed from continuation lines.
            try:
                parsed = json.loads(_strip_log_prefixes(raw))
            except json.JSONDecodeError:
                dropped.append({"reason": "unparseable JSON",
                                "duration_ms": float(m.group(1))})
                continue
        try:
            plan = redact_plan(parsed, level=level)
            assert_clean(plan)
        except Exception as exc:                       # noqa: BLE001
            if strict:
                raise
            dropped.append({"reason": f"redaction failed: {exc}",
                            "duration_ms": float(m.group(1))})
            continue
        out.append({"duration_ms": float(m.group(1)), "plan": plan})
    return out, dropped


# ----------------------------------------------------------- pg_stat_statements

# Deliberately excludes `query` text. The point of this view is to RANK queries, and the
# text is the part that carries user data. queryid is a stable handle that does not.
PGSS_SQL = """
SELECT queryid::text AS query_id,
       calls,
       total_exec_time AS total_ms,
       mean_exec_time  AS mean_ms,
       rows
FROM pg_stat_statements
WHERE calls >= %(min_calls)s
ORDER BY total_exec_time DESC
LIMIT %(limit)s
"""


def rank_queries(rows):
    """
    Order queries by the time they actually consume, and mark a calibration budget.

    Total time, not mean: a 5 ms query called a million times matters more than a 10 s
    report run nightly, and an SLA gate that ignores that is gating the wrong thing.

    `calibration_candidate` marks the top few by total time. These are the queries worth
    spending a production measurement on, because a correction fitted on the workload's
    actual hot set generalises to that workload -- whereas `cetp.operator_model`'s `spread`
    rule needs per-query predicted scalings, which pg_stat_statements cannot provide.
    """
    ranked = sorted(rows, key=lambda r: float(r.get("total_ms") or 0), reverse=True)
    total = sum(float(r.get("total_ms") or 0) for r in ranked) or 1.0
    cum = 0.0
    out = []
    for i, r in enumerate(ranked):
        t = float(r.get("total_ms") or 0)
        cum += t
        out.append({**r,
                    "share_of_total_time": t / total,
                    "cumulative_share": cum / total,
                    # 3 is the measured point where a correction became reliably better
                    # than none; see cetp.calibration_store.MIN_OBSERVATIONS.
                    "calibration_candidate": i < 3})
    return out


def fetch_pg_stat_statements(cur, min_calls=1, limit=200):
    """Run PGSS_SQL on an open cursor. Returns [] with a reason if unavailable."""
    try:
        cur.execute(PGSS_SQL, {"min_calls": min_calls, "limit": limit})
    except Exception as exc:                            # noqa: BLE001
        return [], (f"pg_stat_statements unavailable: {exc}. Add it to "
                    f"shared_preload_libraries and run CREATE EXTENSION "
                    f"pg_stat_statements.")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()], None


__all__ = ["parse_auto_explain_log", "rank_queries", "fetch_pg_stat_statements",
           "PGSS_SQL"]

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, RandomForestClassifier

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
CORRECTED_CSV = PROJECT_DIR / "data" / "corrected" / "tpch_dataset_corrected.csv"
DEFAULT_DB_PATH = PROJECT_DIR / "cetp_measurements.db"
ONLINE_MODELS_DIR = PROJECT_DIR / "models_online"

RANDOM_STATE = 42

SEED_HARDWARE_SIGNATURE = {
    "c5a": {"bandwidth": 14.9, "compute": 0.593},
    "z1d": {"bandwidth": 9.77, "compute": 0.882},
    "r5n": {"bandwidth": 9.39, "compute": 0.705},
    "m5a": {"bandwidth": 10.01, "compute": 0.457},
    "c7i": {"bandwidth": 8.44, "compute": 0.807},
}

BOTTLENECK_LABELS = {
    1: "compute", 2: "mixed", 3: "mixed", 4: "io", 5: "mixed", 6: "bandwidth",
    7: "mixed", 8: "mixed", 9: "mixed", 10: "mixed", 11: "mixed", 12: "compute",
    13: "bandwidth", 14: "bandwidth", 16: "compute", 17: "io", 18: "mixed",
    19: "bandwidth", 20: "io", 21: "mixed", 22: "io",
}

RAW_BOTTLENECK_COLS = ["cost", "shared_hit", "shared_read", "total_buffers", "io_ratio", "rows"]
SCALING_FEATURE_COLUMNS = [
    "dev_cost", "dev_total_buffers", "dev_shared_hit", "dev_shared_read", "dev_rows", "dev_io_ratio",
    "bandwidth_ratio", "compute_ratio",
    "p_compute", "p_bandwidth", "p_io", "p_mixed",
    "analytical_scaling",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS machines (
    machine_id TEXT PRIMARY KEY,
    bandwidth REAL NOT NULL,
    compute REAL NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('seed', 'captured')),
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS measurements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id TEXT NOT NULL REFERENCES machines(machine_id),
    query_id TEXT NOT NULL,
    cost REAL NOT NULL,
    shared_hit REAL NOT NULL,
    shared_read REAL NOT NULL,
    rows REAL NOT NULL,
    time_ms REAL NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('seed', 'captured')),
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS retrain_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    retrained_at TEXT NOT NULL,
    model_version INTEGER NOT NULL,
    n_captured_total INTEGER NOT NULL,
    n_captured_since_prior_retrain INTEGER NOT NULL,
    machines_snapshot TEXT NOT NULL
);
"""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_store(db_path=DEFAULT_DB_PATH, seed_csv=CORRECTED_CSV, hardware_signature=None,
               excluded_machines=None):
    excluded_machines = set(excluded_machines or [])
    hardware_signature = hardware_signature or SEED_HARDWARE_SIGNATURE
    conn = _connect(db_path)
    conn.executescript(SCHEMA)

    existing = conn.execute("SELECT COUNT(*) FROM machines").fetchone()[0]
    if existing > 0:
        conn.close()
        return

    now = _now()
    raw = pd.read_csv(seed_csv)
    raw = raw[~raw["machine_id"].isin(excluded_machines)]
    raw["query_num"] = raw["query_id"].str.extract(r"q(\d+)").astype(int)
    agg = (
        raw.groupby(["machine_id", "query_id"])
        .agg(cost=("cost", "first"), shared_hit=("shared_hit", "median"),
             shared_read=("shared_read", "median"), rows=("rows", "median"),
             time_ms=("time_ms", "median"))
        .reset_index()
    )

    for machine_id, sig in hardware_signature.items():
        if machine_id in excluded_machines:
            continue
        conn.execute(
            "INSERT INTO machines (machine_id, bandwidth, compute, source, added_at) VALUES (?, ?, ?, 'seed', ?)",
            (machine_id, sig["bandwidth"], sig["compute"], now),
        )

    for _, r in agg.iterrows():
        conn.execute(
            "INSERT INTO measurements (machine_id, query_id, cost, shared_hit, shared_read, rows, time_ms, source, captured_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'seed', ?)",
            (r["machine_id"], r["query_id"], float(r["cost"]), float(r["shared_hit"]),
             float(r["shared_read"]), float(r["rows"]), float(r["time_ms"]), now),
        )

    conn.commit()
    conn.close()


def record_measurement(machine_id, query_id, cost, shared_hit, shared_read, rows, time_ms,
                        bandwidth=None, compute=None, db_path=DEFAULT_DB_PATH):
    conn = _connect(db_path)
    known = conn.execute("SELECT 1 FROM machines WHERE machine_id = ?", (machine_id,)).fetchone()
    if known is None:
        if bandwidth is None or compute is None:
            conn.close()
            raise ValueError(
                f"machine_id '{machine_id}' is not registered; a first measurement from a new machine "
                "must supply its static (bandwidth, compute) hardware signature, measured separately "
                "from any query timing -- never derived from a captured prod time."
            )
        conn.execute(
            "INSERT INTO machines (machine_id, bandwidth, compute, source, added_at) VALUES (?, ?, ?, 'captured', ?)",
            (machine_id, float(bandwidth), float(compute), _now()),
        )
        print(f"[measurement_store] registered new machine '{machine_id}' (bandwidth={bandwidth}, compute={compute})")

    conn.execute(
        "INSERT INTO measurements (machine_id, query_id, cost, shared_hit, shared_read, rows, time_ms, source, captured_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'captured', ?)",
        (machine_id, query_id, float(cost), float(shared_hit), float(shared_read), float(rows), float(time_ms), _now()),
    )
    conn.commit()
    conn.close()
    print(f"[measurement_store] recorded measurement: machine={machine_id} query={query_id} time_ms={time_ms:.2f}")


def _last_retrain_timestamp(conn):
    row = conn.execute("SELECT retrained_at FROM retrain_log ORDER BY id DESC LIMIT 1").fetchone()
    return row[0] if row else None


def count_captured_since_last_retrain(db_path=DEFAULT_DB_PATH):
    conn = _connect(db_path)
    last = _last_retrain_timestamp(conn)
    if last is None:
        n = conn.execute("SELECT COUNT(*) FROM measurements WHERE source = 'captured'").fetchone()[0]
    else:
        n = conn.execute(
            "SELECT COUNT(*) FROM measurements WHERE source = 'captured' AND captured_at > ?", (last,)
        ).fetchone()[0]
    conn.close()
    return n


def load_all_machines(db_path=DEFAULT_DB_PATH):
    conn = _connect(db_path)
    rows = conn.execute("SELECT machine_id, bandwidth, compute FROM machines").fetchall()
    conn.close()
    return {m: {"bandwidth": bw, "compute": cp} for m, bw, cp in rows}


def load_all_measurements(db_path=DEFAULT_DB_PATH):
    conn = _connect(db_path)
    df = pd.read_sql_query(
        "SELECT machine_id, query_id, cost, shared_hit, shared_read, rows, time_ms, source FROM measurements",
        conn,
    )
    conn.close()
    return df


def _build_agg(measurements_df):
    agg = (
        measurements_df.groupby(["machine_id", "query_id"])
        .agg(cost=("cost", "median"), shared_hit=("shared_hit", "median"),
             shared_read=("shared_read", "median"), rows=("rows", "median"),
             time_p50=("time_ms", "median"))
        .reset_index()
    )
    agg["query_num"] = agg["query_id"].str.extract(r"q(\d+)").astype(int)
    agg["total_buffers"] = agg["shared_hit"] + agg["shared_read"]
    agg["io_ratio"] = agg["shared_read"] / (agg["total_buffers"] + 1)
    agg["bottleneck"] = agg["query_num"].map(BOTTLENECK_LABELS)
    return agg


def _fit_bottleneck_classifier(agg):
    clf = RandomForestClassifier(n_estimators=300, class_weight="balanced", random_state=RANDOM_STATE)
    clf.fit(agg[RAW_BOTTLENECK_COLS].to_numpy(), agg["bottleneck"].to_numpy())
    return clf


def _build_pairs(agg, hardware_signature_table, bottleneck_model):
    class_order = list(bottleneck_model.classes_)
    machines = sorted(agg["machine_id"].unique())
    pairs = []
    for dev in machines:
        for prod in machines:
            if dev == prod:
                continue
            dd = agg[agg["machine_id"] == dev].set_index("query_id")
            pp = agg[agg["machine_id"] == prod].set_index("query_id")
            common = dd.index.intersection(pp.index)
            for q in common:
                pairs.append({
                    "dev_machine": dev, "prod_machine": prod, "query_id": q,
                    "dev_cost": dd.loc[q, "cost"], "dev_shared_hit": dd.loc[q, "shared_hit"],
                    "dev_shared_read": dd.loc[q, "shared_read"], "dev_total_buffers": dd.loc[q, "total_buffers"],
                    "dev_io_ratio": dd.loc[q, "io_ratio"], "dev_rows": dd.loc[q, "rows"],
                    "dev_time_p50": dd.loc[q, "time_p50"], "prod_time_p50": pp.loc[q, "time_p50"],
                })
    pdf = pd.DataFrame(pairs)
    pdf["scaling_factor_p50"] = pdf["prod_time_p50"] / pdf["dev_time_p50"]
    pdf["bandwidth_ratio"] = pdf["prod_machine"].map(lambda m: hardware_signature_table[m]["bandwidth"]) / \
        pdf["dev_machine"].map(lambda m: hardware_signature_table[m]["bandwidth"])
    pdf["compute_ratio"] = pdf["prod_machine"].map(lambda m: hardware_signature_table[m]["compute"]) / \
        pdf["dev_machine"].map(lambda m: hardware_signature_table[m]["compute"])

    X = pdf[["dev_cost", "dev_shared_hit", "dev_shared_read", "dev_total_buffers", "dev_io_ratio", "dev_rows"]].to_numpy()
    proba = bottleneck_model.predict_proba(X)
    for i, cls in enumerate(class_order):
        pdf[f"p_{cls}"] = proba[:, i]

    def analytical(row):
        invb, invc = 1.0 / row["bandwidth_ratio"], 1.0 / row["compute_ratio"]
        mixed = np.sqrt(invb * invc)
        wb = row["p_bandwidth"] + row["p_io"]
        return wb * invb + row["p_compute"] * invc + row["p_mixed"] * mixed

    pdf["analytical_scaling"] = pdf.apply(analytical, axis=1)
    return pdf


def _fit_quantile_models(pairs_df):
    models = {}
    X = pairs_df[SCALING_FEATURE_COLUMNS].to_numpy()
    y = pairs_df["scaling_factor_p50"].to_numpy()
    for alpha in (0.5, 0.95, 0.99):
        model = GradientBoostingRegressor(
            loss="quantile", alpha=alpha, n_estimators=200, max_depth=3,
            learning_rate=0.05, random_state=RANDOM_STATE,
        )
        model.fit(X, y)
        models[alpha] = model
    return models


def retrain(db_path=DEFAULT_DB_PATH, n_trigger=20, force=False, persist=True):
    n_since = count_captured_since_last_retrain(db_path)
    if not force and n_since < n_trigger:
        print(f"[measurement_store] retrain skipped: {n_since}/{n_trigger} captured measurements since last retrain")
        return None

    conn = _connect(db_path)
    hardware_signature_table = load_all_machines(db_path)
    measurements_df = load_all_measurements(db_path)
    n_captured_total = int((measurements_df["source"] == "captured").sum())
    conn.close()

    agg = _build_agg(measurements_df)
    bottleneck_model = _fit_bottleneck_classifier(agg)
    pairs_df = _build_pairs(agg, hardware_signature_table, bottleneck_model)
    quantile_models = _fit_quantile_models(pairs_df)

    conn = _connect(db_path)
    model_version = conn.execute("SELECT COUNT(*) FROM retrain_log").fetchone()[0] + 1
    conn.execute(
        "INSERT INTO retrain_log (retrained_at, model_version, n_captured_total, n_captured_since_prior_retrain, machines_snapshot) "
        "VALUES (?, ?, ?, ?, ?)",
        (_now(), model_version, n_captured_total, n_since, json.dumps(sorted(hardware_signature_table.keys()))),
    )
    conn.commit()
    conn.close()

    if persist:
        version_dir = ONLINE_MODELS_DIR / f"v{model_version}"
        version_dir.mkdir(parents=True, exist_ok=True)
        joblib.dump(bottleneck_model, version_dir / "bottleneck_classifier.pkl")
        joblib.dump(quantile_models, version_dir / "scaling_quantile_models.pkl")
        joblib.dump(hardware_signature_table, version_dir / "hardware_signature.pkl")
        (version_dir / "scaling_feature_columns.json").write_text(json.dumps(SCALING_FEATURE_COLUMNS, indent=2))

    print(
        f"[measurement_store] RETRAINED (version {model_version}): "
        f"{n_since} new captured measurements folded in ({n_captured_total} captured total), "
        f"known machines = {sorted(hardware_signature_table.keys())}"
    )
    return {
        "model_version": model_version,
        "bottleneck_model": bottleneck_model,
        "quantile_models": quantile_models,
        "hardware_signature_table": hardware_signature_table,
        "feature_columns": SCALING_FEATURE_COLUMNS,
    }

import re
import json
import numpy as np
import pandas as pd
import joblib
from pathlib import Path
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.metrics import precision_recall_fscore_support, confusion_matrix, f1_score

PROJECT_DIR = Path("/Users/harshithj/Main/Resources/CETP")
CSV_PATH = PROJECT_DIR / "tpch_dataset.csv"
MODELS_DIR = PROJECT_DIR / "models"
MODELS_DIR.mkdir(exist_ok=True)

RANDOM_STATE = 42

BOTTLENECK_LABELS = {
    1: "compute", 2: "mixed", 3: "mixed", 4: "io", 5: "mixed", 6: "bandwidth",
    7: "mixed", 8: "mixed", 9: "mixed", 10: "mixed", 11: "mixed", 12: "compute",
    13: "bandwidth", 14: "bandwidth", 16: "compute", 17: "io", 18: "mixed",
    19: "bandwidth", 20: "io", 21: "mixed", 22: "io",
}

FEATURE_COLS = ["cost", "shared_hit", "shared_read", "total_buffers", "io_ratio", "rows"]

raw = pd.read_csv(CSV_PATH)
raw["query_num"] = raw["query_id"].str.extract(r"q(\d+)").astype(int)

agg = (
    raw.groupby(["machine_id", "query_id", "query_num"])
    .agg(
        cost=("cost", "first"),
        time_ms=("time_ms", "median"),
        shared_hit=("shared_hit", "median"),
        shared_read=("shared_read", "median"),
        rows=("rows", "median"),
    )
    .reset_index()
)

agg["total_buffers"] = agg["shared_hit"] + agg["shared_read"]
agg["io_ratio"] = agg["shared_read"] / (agg["total_buffers"] + 1)
agg["bottleneck"] = agg["query_num"].map(BOTTLENECK_LABELS)

missing = agg[agg["bottleneck"].isna()]
if len(missing):
    raise ValueError(f"Unlabeled query numbers present: {sorted(missing['query_num'].unique())}")

X = agg[FEATURE_COLS].to_numpy()
y = agg["bottleneck"].to_numpy()
groups = agg["query_num"].to_numpy()

logo = LeaveOneGroupOut()
y_pred = np.empty_like(y)

for train_idx, test_idx in logo.split(X, y, groups):
    clf = RandomForestClassifier(
        n_estimators=300,
        class_weight="balanced",
        random_state=RANDOM_STATE,
    )
    clf.fit(X[train_idx], y[train_idx])
    y_pred[test_idx] = clf.predict(X[test_idx])

class_labels = sorted(BOTTLENECK_LABELS.values(), key=lambda c: ["compute", "bandwidth", "io", "mixed"].index(c))
class_labels = ["compute", "bandwidth", "io", "mixed"]

macro_f1 = f1_score(y, y_pred, labels=class_labels, average="macro")
precision, recall, f1, support = precision_recall_fscore_support(
    y, y_pred, labels=class_labels, zero_division=0
)
cm = confusion_matrix(y, y_pred, labels=class_labels)

print("=" * 72)
print("LEAVE-ONE-QUERY-OUT CROSS-VALIDATION RESULTS")
print("=" * 72)
print(f"\nMacro-averaged F1 (headline metric): {macro_f1:.3f}\n")

print("Per-class precision / recall / F1 (support = row count, 5 rows/query):")
print(f"{'class':<10}{'precision':>10}{'recall':>10}{'f1':>10}{'support':>10}")
for cls, p, r, f, s in zip(class_labels, precision, recall, f1, support):
    print(f"{cls:<10}{p:>10.3f}{r:>10.3f}{f:>10.3f}{s:>10d}")

print("\nConfusion matrix (rows = true, cols = predicted):")
header = " " * 10 + "".join(f"{c:>10}" for c in class_labels)
print(header)
for cls, row in zip(class_labels, cm):
    print(f"{cls:<10}" + "".join(f"{v:>10d}" for v in row))

agg["pred_bottleneck"] = y_pred
misclassified = agg[agg["bottleneck"] != agg["pred_bottleneck"]]
mis_by_query = (
    misclassified.groupby(["query_num", "bottleneck"])["pred_bottleneck"]
    .agg(lambda s: s.value_counts().idxmax())
    .reset_index()
    .sort_values("query_num")
)

print("\nMisclassified queries (true -> most common predicted label across its 5 machine rows):")
if len(mis_by_query) == 0:
    print("  none")
else:
    for _, row in mis_by_query.iterrows():
        n_wrong = len(misclassified[misclassified["query_num"] == row["query_num"]])
        print(f"  q{row['query_num']}: true={row['bottleneck']:<10} predicted={row['pred_bottleneck']:<10} ({n_wrong}/5 rows misclassified)")

final_model = RandomForestClassifier(
    n_estimators=300,
    class_weight="balanced",
    random_state=RANDOM_STATE,
)
final_model.fit(X, y)

model_path = MODELS_DIR / "bottleneck_classifier.pkl"
labels_path = MODELS_DIR / "bottleneck_labels.json"
joblib.dump(final_model, model_path)
with open(labels_path, "w") as f:
    json.dump(BOTTLENECK_LABELS, f, indent=2)

print(f"\nSaved model to {model_path}")
print(f"Saved label mapping to {labels_path}")


def predict_bottleneck(cost, shared_hit, shared_read, rows, total_buffers, io_ratio):
    model = joblib.load(MODELS_DIR / "bottleneck_classifier.pkl")
    x = np.array([[cost, shared_hit, shared_read, total_buffers, io_ratio, rows]])
    proba = model.predict_proba(x)[0]
    idx = np.argmax(proba)
    return model.classes_[idx], float(proba[idx])


if __name__ == "__main__":
    example = agg.iloc[0]
    cls, conf = predict_bottleneck(
        cost=example["cost"],
        shared_hit=example["shared_hit"],
        shared_read=example["shared_read"],
        rows=example["rows"],
        total_buffers=example["total_buffers"],
        io_ratio=example["io_ratio"],
    )
    print(f"\nExample predict_bottleneck() call on q{example['query_num']} ({example['machine_id']}):")
    print(f"  true label = {example['bottleneck']}, predicted = {cls}, confidence = {conf:.3f}")

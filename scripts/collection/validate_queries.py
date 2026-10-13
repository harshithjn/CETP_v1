import json, sys, subprocess
import pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fingerprint import fingerprint
QD = Path("scripts/collection/queries")
p = pd.read_csv("results/plan_structure_features.csv").set_index("query_id")
nums = [1,2,3,4,5,6,7,8,9,10,11,12,13,14,16,17,18,19,20,21,22]
rows = []
for n in nums:
    qid = f"q{n}"
    sql = "\n".join(l for l in (QD/f"{qid}.sql").read_text().splitlines()
                    if not l.strip().startswith("--"))
    f, err = fingerprint(sql)
    t = p.loc[qid]
    fields = {"rows_processed": int(t.rows_processed), "node_count": int(t.node_count),
              "plan_depth": int(t.plan_depth), "root_actual_rows": int(t.root_actual_rows),
              "root_cost": float(t.root_cost)}
    match = {k: (abs(f[k]-v) < 0.01 if k == "root_cost" else f[k] == v) for k, v in fields.items()}
    rows.append({"query_id": qid, **{f"{k}_target": v for k, v in fields.items()},
                 **{f"{k}_actual": f[k] for k in fields},
                 "fields_matched": sum(match.values()), "exact": all(match.values())})
    print(f"{qid:<5} {sum(match.values())}/5 {'EXACT' if all(match.values()) else 'APPROX: ' + ','.join(k for k,v in match.items() if not v)}")
df = pd.DataFrame(rows)
df.to_csv("results/query_reconstruction_validation.csv", index=False)
print(f"\n{df.exact.sum()}/21 exact on all 5 fingerprint fields")

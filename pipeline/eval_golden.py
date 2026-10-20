"""Golden-set evaluation: read Kim's hand labels from the Google Sheet CSV export, compare
against model predictions, produce per-case and per-field results. Golden labels are never
model inputs. Run after the main classify run and after Kim finishes labeling.
"""
import csv, io, json, sqlite3, sys, urllib.request
from pathlib import Path

SHEET_ID = "1IbtB_lo1V5RKwas8zQfyQG5gWTtNG6EG_Ey4dBgX1Yo"

def fetch_golden(sheet_id=SHEET_ID, gid="0"):
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
    with urllib.request.urlopen(url) as r:
        rows = list(csv.DictReader(io.StringIO(r.read().decode("utf-8"))))
    golden = {}
    for row in rows:
        rid = (row.get("review_id") or "").strip()
        t = (row.get("topic") or "").strip().lower()
        i = (row.get("intent") or "").strip().lower()
        s = (row.get("severity") or "").strip()
        if rid and t and i and s.isdigit():
            golden[rid] = {"topic": t, "intent": i, "severity": int(s)}
    return golden

def compare(db_path, out_dir="evals"):
    golden = fetch_golden()
    Path(out_dir).mkdir(exist_ok=True)
    con = sqlite3.connect(db_path)
    cases, field_ok = [], {"topic": 0, "intent": 0, "severity": 0}
    n = 0
    for rid, gold in golden.items():
        row = con.execute("SELECT topic, intent, severity FROM enriched WHERE review_id=?", (rid,)).fetchone()
        n += 1
        if not row:
            cases.append({"review_id": rid, "golden": gold, "predicted": None, "note": "not yet classified"})
            continue
        pred = {"topic": row[0], "intent": row[1], "severity": row[2]}
        match = {f: pred[f] == gold[f] for f in field_ok}
        for f, ok in match.items():
            field_ok[f] += int(ok)
        cases.append({"review_id": rid, "golden": gold, "predicted": pred,
                      "field_match": match, "exact": all(match.values())})
    con.close()
    done = [c for c in cases if c["predicted"]]
    summary = {"golden_labeled": n, "predicted": len(done),
               "accuracy": {f: (field_ok[f] / len(done) if done else None) for f in field_ok},
               "exact_match": (sum(c.get("exact", False) for c in done) / len(done) if done else None)}
    json.dump({"summary": summary, "cases": cases}, open(f"{out_dir}/golden_eval.json", "w"), indent=2)
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    compare(sys.argv[1] if len(sys.argv) > 1 else "runs/pipeline.db")

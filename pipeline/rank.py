"""Stage 5 - Rank: deterministic. Contract format (GRADING_CONTRACT v1):
rank,issue_id,complaint_count,severity_sum,mean_severity,priority_score
priority_score = severity_sum; complaint/cancellation records only; each review counted once
per issue; sort by priority_score desc, issue_id asc; means 6-decimal ROUND_HALF_UP.
No model calls. Rerunnable from saved membership + enriched records (byte-identical output).
"""
import csv, os, sys
from decimal import Decimal, ROUND_HALF_UP
from db import DB

def six_dp(x):
    return str(Decimal(x).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))

def rank_rows(db):
    return db.execute("""
        SELECT m.issue_id, COUNT(*) AS c, SUM(e.severity) AS ss
        FROM membership m JOIN enriched e ON e.review_id = m.review_id
        WHERE e.intent IN ('complaint','cancellation')
        GROUP BY m.issue_id
        ORDER BY ss DESC, m.issue_id ASC""").fetchall()

def rank(db_path, out_dir):
    db = DB(db_path)
    rows = rank_rows(db)
    os.makedirs(out_dir, exist_ok=True)
    with open(f"{out_dir}/ranking.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "issue_id", "complaint_count", "severity_sum", "mean_severity", "priority_score"])
        for i, (iid, c, ss) in enumerate(rows, 1):
            w.writerow([i, iid, str(c), str(ss), six_dp(Decimal(ss) / Decimal(c)), str(ss)])
    with open(f"{out_dir}/aggregates.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["issue_id", "complaint_count", "severity_sum", "mean_severity"])
        for iid, c, ss in rows:
            w.writerow([iid, str(c), str(ss), six_dp(Decimal(ss) / Decimal(c))])
    print(f"rank: {len(rows)} issues -> {out_dir}/ranking.csv")

if __name__ == "__main__":
    rank(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "runs")

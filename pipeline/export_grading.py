"""Export the grading/ folder exactly per GRADING_CONTRACT.md (a5-audit-v1)."""
import csv, json, os, sys, time
from decimal import Decimal, ROUND_HALF_UP
from db import DB
from classify import SCHEMA_VERSION

VERSION = "a5-audit-v1"
ANALYSIS_SHA256 = "1fc85de68a304dd8978b537cfa58793d5f41cbaf417fa32cb53899f83a2fcef6"
ANALYSIS_COUNT = 660_622

def label_config(model, prompt_version):
    return f"{model}+{prompt_version}+{SCHEMA_VERSION}"

def six_dp(x):
    return str(Decimal(str(x)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))

def export(db_path, out_dir, run_id, phase="initial"):
    db = DB(db_path)
    os.makedirs(out_dir, exist_ok=True)
    json.dump({"version": VERSION, "analysis_count": ANALYSIS_COUNT,
               "analysis_sha256": ANALYSIS_SHA256,
               "classification_input_fields": ["review_text"],
               "allow_multi_issue": False},
              open(f"{out_dir}/run.json", "w"), indent=2)
    n_done = n_quar = 0
    with open(f"{out_dir}/records.jsonl", "w") as f:
        for r in db.execute("""SELECT s.review_id, s.source_sha256, s.status, s.quarantine_reason,
                e.topic, e.intent, e.sentiment, e.severity, e.entities, e.evidence_quote,
                e.needs_review, e.label_config, e.cache_source_id, e.model, e.prompt_version
                FROM source_reviews s LEFT JOIN enriched e ON e.review_id = s.review_id
                ORDER BY s.review_id"""):
            (rid, sha, status, reason, topic, intent, sent, sev, ents, quote, nr, lc, csrc, model, pv) = r
            if status == "completed" and topic:
                rec = {"review_id": rid, "source_sha256": sha, "status": "completed",
                       "topic": topic, "intent": intent, "sentiment": sent, "severity": sev,
                       "entities": json.loads(ents), "evidence_quote": quote,
                       "needs_review": bool(nr),
                       "label_config": label_config(model or "unknown", pv or "unknown")}
                if csrc: rec["cache_source_id"] = csrc
                n_done += 1
            else:
                rec = {"review_id": rid, "source_sha256": sha, "status": "quarantined",
                       "reason": reason or "unprocessed_budget_or_time_cap"}
                n_quar += 1
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
    with open(f"{out_dir}/membership.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["issue_id", "review_id"])
        for iid, rid in db.execute("SELECT issue_id, review_id FROM membership ORDER BY issue_id, review_id"):
            w.writerow([iid, rid])
    rows = db.execute("""SELECT m.issue_id, COUNT(*) c, SUM(e.severity) ss
        FROM membership m JOIN enriched e ON e.review_id=m.review_id
        WHERE e.intent IN ('complaint','cancellation')
        GROUP BY m.issue_id ORDER BY ss DESC, m.issue_id ASC""").fetchall()
    with open(f"{out_dir}/ranking.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "issue_id", "complaint_count", "severity_sum", "mean_severity", "priority_score"])
        for i, (iid, c, ss) in enumerate(rows, 1):
            mean = six_dp(Decimal(ss) / Decimal(c))
            w.writerow([i, iid, str(c), str(ss), mean, str(ss)])
    with open(f"{out_dir}/claims.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["claim_id", "issue_id", "metric", "value"])
        n = 0
        for iid, c, ss in rows[:8]:
            mean = six_dp(Decimal(ss) / Decimal(c))
            for metric, val in (("complaint_count", str(c)), ("severity_sum", str(ss)),
                                ("mean_severity", mean), ("priority_score", str(ss))):
                n += 1; w.writerow([f"C{n:03d}", iid, metric, val])
    with open(f"{out_dir}/calls.jsonl", "w") as f:
        for (cid, ts, rn, stage, model, effort, pv, sv, ids, status, itok, ctok, otok) in db.execute(
            """SELECT call_id, ts, run_id, stage, model, effort, prompt_version, schema_version,
                      input_ids, status, input_tokens, cached_input_tokens, output_tokens
               FROM calls ORDER BY call_id"""):
            role = {"classify": "enrich", "verify": "verify", "group": "group", "memo": "memo"}.get(stage, stage)
            rec = {"request_id": f"call-{cid}", "role": role,
                   "review_ids": json.loads(ids) if ids else [],
                   "model": model, "phase": "resume" if "resume" in (rn or "") else "initial",
                   "outcome": "succeeded" if status == "ok" else "failed",
                   "label_config": label_config(model or "unknown", pv or "unknown"),
                   "input_tokens": itok, "output_tokens": otok}
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    print(f"exported: {n_done} completed, {n_quar} quarantined, {len(rows)} issues -> {out_dir}")

if __name__ == "__main__":
    export(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "run")

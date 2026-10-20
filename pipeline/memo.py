"""Stage 6 - Recommend: memo from saved aggregates + bounded evidence pack only.
Code checks every cited issue ID and number against saved calculations.
"""
import json, os, sys, time
from db import DB
from llm_openai import respond, load_rates, cost_of
from classify import ledger_call

MEMO_SCHEMA = {"type": "object", "additionalProperties": False,
  "properties": {
    "recommendation": {"type": "string"},
    "priority_issue_id": {"type": "string"},
    "alternatives": {"type": "array", "items": {"type": "string"}},
    "claims": {"type": "array", "items": {"type": "object", "additionalProperties": False,
        "properties": {"issue_id": {"type": "string"}, "metric": {"type": "string"}, "value": {"type": "number"}},
        "required": ["issue_id", "metric", "value"]}},
    "evidence_review_ids": {"type": "array", "items": {"type": "string"}},
    "limitations": {"type": "array", "items": {"type": "string"}},
  },
  "required": ["recommendation", "priority_issue_id", "alternatives", "claims", "evidence_review_ids", "limitations"]}

def run_memo(db_path, model, effort, api_key, rates_path, run_id, out_path="runs/memo.md"):
    db = DB(db_path); rates = load_rates(rates_path)
    ranking = db.execute("""SELECT m.issue_id, i.name, COUNT(*) c, SUM(e.severity) ss,
        ROUND(AVG(CAST(e.severity AS REAL)),6) ms
        FROM membership m JOIN enriched e ON e.review_id=m.review_id
        JOIN issues i ON i.issue_id=m.issue_id
        WHERE e.intent IN ('complaint','cancellation')
        GROUP BY m.issue_id ORDER BY ss DESC, m.issue_id ASC""").fetchall()
    totals = db.execute("""SELECT
        (SELECT COUNT(*) FROM enriched WHERE status='completed'),
        (SELECT COUNT(*) FROM source_reviews WHERE status='quarantined'),
        (SELECT COUNT(*) FROM source_reviews)""").fetchone()
    top = ranking[:8]
    pack = {"scope": {"completed": totals[0], "quarantined": totals[1], "source_rows": totals[2]},
            "ranking": [{"issue_id": r[0], "name": r[1], "complaint_count": r[2],
                         "severity_sum": r[3], "mean_severity": r[4]} for r in top],
            "evidence": {}}
    for r in top[:5]:
        ex = db.execute("""SELECT m.review_id, s.review_text FROM membership m
            JOIN source_reviews s ON s.review_id=m.review_id
            JOIN enriched e ON e.review_id=m.review_id
            WHERE m.issue_id=? ORDER BY e.severity DESC, m.review_id LIMIT 3""", (r[0],)).fetchall()
        pack["evidence"][r[0]] = [{"review_id": rid, "text": t[:300]} for rid, t in ex]
    sys_p = """You are the MEMO role in a review-analysis pipeline. You receive ONLY saved aggregates and a bounded evidence pack; you never see the raw dataset. Advise Spotify: where should next quarter's product effort go - access, usability, playback, or billing/support?
Rules: recommend one priority issue, compare at least two alternatives, cite only issue IDs and numbers present in the input, list evidence review IDs only from the input, and state limitations (self-selected reviews, historical window, intent is expressed not observed churn, no revenue data). Every claim must use metrics complaint_count, severity_sum, mean_severity, or priority_score with exact values from the input."""
    parsed, usage = respond(sys_p, json.dumps(pack), model, effort, MEMO_SCHEMA, api_key, max_output_tokens=3000)
    cost = cost_of(model, usage, rates)
    ledger_call(db, run_id, "memo", model, effort, [], "ok", 1, usage, cost, None)
    if cost: db.execute("INSERT INTO spend_ledger VALUES(?,?,?,?)", (time.time(), run_id, cost, "memo"))
    valid_issues = {r[0]: {"complaint_count": r[2], "severity_sum": r[3], "mean_severity": r[4], "priority_score": r[3]}
                    for r in ranking}
    valid_rids = {rid for ev in pack["evidence"].values() for rid in [e["review_id"] for e in ev]}
    problems = []
    if parsed.get("priority_issue_id") not in valid_issues:
        problems.append(f"priority_issue_id {parsed.get('priority_issue_id')} not in saved ranking")
    for c in parsed.get("claims", []):
        iid = c["issue_id"]
        if iid not in valid_issues:
            problems.append(f"claim cites unknown issue {iid}"); continue
        allowed = valid_issues[iid].get(c["metric"])
        if allowed is None:
            problems.append(f"claim metric {c['metric']} unsupported"); continue
        if abs(float(c["value"]) - float(allowed)) > (0.000001 if c["metric"] == "mean_severity" else 0.5):
            problems.append(f"claim {iid}/{c['metric']}={c['value']} != saved {allowed}")
    for rid in parsed.get("evidence_review_ids", []):
        if rid not in valid_rids:
            problems.append(f"evidence review {rid} not in pack")
    lines = ["# Decision Memo - Spotify Product Priority", "",
             f"**Recommendation:** {parsed['recommendation']}", "",
             f"**Priority issue:** {parsed['priority_issue_id']}", "",
             "**Alternatives considered:**"]
    lines += [f"- {a}" for a in parsed["alternatives"]]
    lines += ["", "**Claims (validated against saved calculations):**"]
    for c in parsed["claims"]:
        lines.append(f"- {c['issue_id']} {c['metric']} = {c['value']}")
    lines += ["", "**Evidence review IDs:** " + ", ".join(parsed["evidence_review_ids"][:12])]
    lines += ["", "**Limitations:**"] + [f"- {l}" for l in parsed["limitations"]]
    if problems:
        lines += ["", "**Code validation flags:**"] + [f"- {p}" for p in problems]
    open(out_path, "w").write("\n".join(lines))
    print(f"memo written to {out_path}; validation problems: {len(problems)}", flush=True)
    for p in problems: print("  FLAG:", p)
    return parsed, problems

if __name__ == "__main__":
    run_memo(sys.argv[1], sys.argv[2], sys.argv[3], os.environ["OPENAI_API_KEY"], "cost/rates.csv", sys.argv[4])

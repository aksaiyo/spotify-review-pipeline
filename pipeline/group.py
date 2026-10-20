"""Stage 4 - Group: deterministic issue building from topic+entity; optional LLM naming.
Membership saved so reranking is deterministic. role=group for the naming call.
"""
import json, os, re, sys, time
from db import DB
from llm_openai import respond, load_rates, cost_of

NAME_SCHEMA = {"type": "object",
    "properties": {"names": {"type": "array", "items": {"type": "object", "additionalProperties": False,
        "properties": {"issue_id": {"type": "string"}, "name": {"type": "string"}},
        "required": ["issue_id", "name"]}}},
    "required": ["names"], "additionalProperties": False}

def slug(e):
    s = re.sub(r"[^a-z0-9]+", "-", e.lower()).strip("-")
    return s[:40] or "general"

def build_issues(db_path, run_id, model=None, effort="none", api_key=None, rates_path="cost/rates.csv",
                 min_entity_count=3):
    db = DB(db_path)
    db.execute("DELETE FROM membership"); db.execute("DELETE FROM issues")
    rows = db.execute("""SELECT review_id, topic, entities FROM enriched
        WHERE status='completed' AND intent IN ('complaint','cancellation')""").fetchall()
    # count entities across complaint/cancellation records
    from collections import Counter
    ent_counts = Counter()
    parsed_rows = []
    for rid, topic, ents in rows:
        ents = [e for e in json.loads(ents) if e.strip()]
        parsed_rows.append((rid, topic, ents))
        for e in ents:
            ent_counts[e.lower()] += 1
    n_mem = 0
    for rid, topic, ents in parsed_rows:
        primary = next((e for e in ents if ent_counts[e.lower()] >= min_entity_count), None)
        issue_id = f"{topic}-{slug(primary)}" if primary else f"{topic}-general"
        db.execute("INSERT OR IGNORE INTO issues VALUES(?,?,?,?)",
                   (issue_id, f"{topic}: {primary}" if primary else f"{topic}: general",
                    "topic+primary_entity" if primary else "topic fallback", time.time()))
        db.execute("INSERT OR IGNORE INTO membership VALUES(?,?)", (issue_id, rid))
        n_mem += 1
    n_issues = db.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
    print(f"group: {n_mem} memberships across {n_issues} issues from {len(rows)} complaint/cancellation records", flush=True)
    # optional: model names issues from bounded examples (cheap, one call)
    if model and api_key:
        rates = load_rates(rates_path)
        pack = []
        for (iid,) in db.execute("SELECT issue_id FROM issues ORDER BY issue_id").fetchall():
            ex = db.execute("""SELECT s.review_text FROM membership m
                JOIN source_reviews s ON s.review_id=m.review_id
                WHERE m.issue_id=? LIMIT 3""", (iid,)).fetchall()
            pack.append({"issue_id": iid, "examples": [t[0][:200] for t in ex]})
        sys_p = ("You are the GROUPING role. Given issue IDs built from topic+entity and a few example reviews each, "
                 "propose a short human-readable name (<=8 words) per issue. Return JSON {\"names\": [{\"issue_id\": ..., \"name\": ...}]}. No commentary.")
        user = json.dumps(pack)[:12000]
        try:
            parsed, usage = respond(sys_p, user, model, effort, NAME_SCHEMA, api_key, max_output_tokens=2000)
            cost = cost_of(model, usage, rates)
            from classify import ledger_call
            ledger_call(db, run_id, "group", model, effort, [], "ok", 1, usage, cost, None)
            if cost: db.execute("INSERT INTO spend_ledger VALUES(?,?,?,?)", (time.time(), run_id, cost, "group naming"))
            for pair in (parsed.get("names") or []):
                iid, name = pair.get("issue_id"), pair.get("name")
                db.execute("UPDATE issues SET name=? WHERE issue_id=?", (str(name)[:120], iid))
            print(f"group: model named {len(parsed.get('names') or [])} issues (cost ${cost or 0:.4f})", flush=True)
        except Exception as e:
            print(f"group naming failed (non-fatal): {str(e)[:200]}", flush=True)

if __name__ == "__main__":
    build_issues(sys.argv[1], sys.argv[2], model=sys.argv[3] if len(sys.argv) > 3 else None,
                 api_key=os.environ.get("OPENAI_API_KEY"))

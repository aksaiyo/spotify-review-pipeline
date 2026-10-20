"""Stage 3 - Verify: independent verifier on a deterministic sample.
The verifier gets the original text + rubric (never the enricher's answer); code compares.
Outputs agreement stats + disagreement list to evals/.
"""
import hashlib, json, os, sys, time
from db import DB
from schema import normalize_record
from classify import ENRICH_SCHEMA, ledger_call
from llm_openai import respond, load_rates, cost_of

SAMPLE_N = 500
SAMPLE_SEED = 42

VERIFY_PROMPT = open("prompts/enrich_v1.md").read() if os.path.exists("prompts/enrich_v1.md") else ""

def sample_ids(db, n, seed):
    rows = db.execute("SELECT review_id, text_sha256 FROM source_reviews WHERE status='completed'").fetchall()
    keyed = sorted(rows, key=lambda r: hashlib.sha256(f"{seed}|{r[0]}".encode()).hexdigest())
    return [r[0] for r in keyed[:n]]

def run_verify(db_path, model, effort, api_key, rates_path, run_id, n=SAMPLE_N, seed=SAMPLE_SEED, batch_size=50):
    db = DB(db_path)
    rates = load_rates(rates_path)
    sys_p = ("You are the VERIFY role in a review-analysis pipeline, independent of the original "
             "labeler. Classify each review from scratch using the same rubric.\n\n"
             + open("prompts/enrich_v1.md").read())
    ids = sample_ids(db, n, seed)
    print(f"verify: sample of {len(ids)} completed records (seed {seed})", flush=True)
    total_cost = 0.0
    compared = {"topic": [0, 0], "intent": [0, 0], "severity": [0, 0]}
    sent_err = []
    disagreements = []
    for i in range(0, len(ids), batch_size):
        chunk_ids = ids[i:i + batch_size]
        marks = ",".join("?" * len(chunk_ids))
        rows = db.execute(f"""SELECT s.review_id, s.review_text, e.topic, e.intent, e.severity, e.sentiment
            FROM source_reviews s JOIN enriched e ON e.review_id=s.review_id
            WHERE s.review_id IN ({marks})""", chunk_ids).fetchall()
        user = "Classify these reviews:\n" + "\n".join(
            f'<review id="{rid}">\n{text}\n</review>' for rid, text, *_ in rows)
        try:
            parsed, usage = respond(sys_p, user, model, effort, ENRICH_SCHEMA, api_key, max_output_tokens=8000)
            cost = cost_of(model, usage, rates); total_cost += cost or 0
            chunk = [((rid, text, ""), "", []) for rid, text, *_ in rows]
            ledger_call(db, run_id, "verify", model, effort, chunk, "ok", 1, usage, cost, None)
            if cost: db.execute("INSERT INTO spend_ledger VALUES(?,?,?,?)", (time.time(), run_id, cost, "verify batch"))
        except Exception as e:
            chunk = [((rid, text, ""), "", []) for rid, text, *_ in rows]
            ledger_call(db, run_id, "verify", model, effort, chunk, "error", 1, None, None, str(e)[:500])
            print(f"verify batch failed (skipped): {str(e)[:120]}", flush=True)
            continue
        results = {r["review_id"]: r for r in parsed.get("results", [])}
        for rid, text, topic, intent, sev, sent in rows:
            v = results.get(rid)
            if not v: continue
            for field, theirs in (("topic", topic), ("intent", intent), ("severity", sev)):
                compared[field][1] += 1
                if v.get(field) == theirs:
                    compared[field][0] += 1
            if isinstance(v.get("sentiment"), (int, float)):
                sent_err.append(abs(float(v["sentiment"]) - float(sent)))
            if v.get("topic") != topic or v.get("intent") != intent or v.get("severity") != sev:
                disagreements.append({"review_id": rid,
                    "enrich": {"topic": topic, "intent": intent, "severity": sev},
                    "verify": {"topic": v.get("topic"), "intent": v.get("intent"), "severity": v.get("severity")}})
    n_comp = compared["topic"][1] or 1
    out = {
        "run_id": run_id, "sample": len(ids), "compared": compared["topic"][1], "seed": seed,
        "agreement": {f: round(c[0] / n_comp, 4) for f, c in compared.items()},
        "sentiment_mae": round(sum(sent_err) / len(sent_err), 4) if sent_err else None,
        "disagreement_count": len(disagreements),
        "disagreements": disagreements[:50],
        "verify_cost_usd": round(total_cost, 6),
    }
    os.makedirs("evals", exist_ok=True)
    json.dump(out, open(f"evals/verify_{run_id}.json", "w"), indent=2)
    print(json.dumps({k: v for k, v in out.items() if k != "disagreements"}, indent=2), flush=True)
    return out

if __name__ == "__main__":
    run_verify(sys.argv[1], sys.argv[2], sys.argv[3], os.environ["OPENAI_API_KEY"], "cost/rates.csv", sys.argv[4])

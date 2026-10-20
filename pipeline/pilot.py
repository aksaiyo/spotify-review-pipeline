"""Run the required 100-review pilot: cold run (empty result cache) + warm run (saved cache).
Produces cost/pilot_records.jsonl, cost/pilot_calls.jsonl, cost/pilot_timing.json.
Paid command - requires OPENAI_API_KEY in env. Offline replay: cost/calculator.py.
"""
import json, os, shutil, sqlite3, sys, time
from pathlib import Path

def mark(rec, stage, t0):
    rec[stage] = round(time.time() - t0, 2)

def run_pilot(model="gpt-6-luna", effort="none", verify_n=20):
    import ingest, classify, verify, group, rank
    db_path = "runs/pilot.db"
    for suf in ("", "-shm", "-wal"):
        Path(db_path + suf).unlink(missing_ok=True)
    timing = {}
    t0 = time.time()
    print("== pilot: ingest cost_100.csv", flush=True)
    ingest.ingest("data/cost_100.csv", db_path, "runs/pilot_out"); mark(timing, "ingest_s", t0)
    t0 = time.time()
    print("== pilot: classify (cold)", flush=True)
    classify.run(db_path, model, effort, os.environ["OPENAI_API_KEY"], "cost/rates.csv",
                 "pilot-cold", batch_size=50, spend_cap=9.0)
    mark(timing, "classify_cold_s", t0)
    t0 = time.time()
    print("== pilot: verify sample", flush=True)
    verify.run_verify(db_path, model, effort, os.environ["OPENAI_API_KEY"], "cost/rates.csv",
                      "pilot-verify", n=verify_n)
    mark(timing, "verify_s", t0)
    t0 = time.time()
    print("== pilot: group + rank", flush=True)
    group.build_issues(db_path, "pilot-group", model=model, effort=effort,
                       api_key=os.environ["OPENAI_API_KEY"], min_entity_count=3)
    rank.rank(db_path, "runs/pilot_out"); mark(timing, "group_rank_s", t0)
    t0 = time.time()
    warm_path = "runs/pilot_warm.db"
    for suf in ("", "-shm", "-wal"):
        Path(warm_path + suf).unlink(missing_ok=True)
    shutil.copy(db_path, warm_path)
    con = sqlite3.connect(warm_path, isolation_level=None)
    con.execute("DELETE FROM enriched")
    con.execute("UPDATE source_reviews SET status='pending', attempts=0 WHERE status='completed'")
    con.close()
    classify.run(warm_path, model, effort, os.environ["OPENAI_API_KEY"], "cost/rates.csv",
                 "pilot-warm", batch_size=50, spend_cap=9.0)
    con = sqlite3.connect(warm_path)
    n1 = con.execute("SELECT COUNT(*) FROM calls WHERE stage='classify' AND run_id='pilot-warm'").fetchone()[0]
    con.close()
    mark(timing, "classify_warm_s", t0)
    timing["warm_new_enrichment_calls"] = n1
    export_pilot(db_path, warm_path)
    json.dump(timing, open("cost/pilot_timing.json", "w"), indent=2)
    print(json.dumps(timing, indent=2), flush=True)

def export_pilot(db_path, warm_path):
    Path("cost").mkdir(exist_ok=True)
    with open("cost/pilot_records.jsonl", "w") as f:
        con = sqlite3.connect(db_path)
        for r in con.execute("""SELECT s.review_id, s.source_sha256, s.status, s.quarantine_reason,
                e.topic, e.intent, e.sentiment, e.severity, e.entities, e.evidence_quote,
                e.needs_review, e.label_config, e.cache_source_id, e.model, e.prompt_version
                FROM source_reviews s LEFT JOIN enriched e ON e.review_id=s.review_id ORDER BY s.review_id"""):
            (rid, sha, status, reason, topic, intent, sent, sev, ents, quote, nr, lc, csrc, model, pv) = r
            rec = {"review_id": rid, "source_sha256": sha, "status": status}
            if status == "completed" and topic:
                rec.update({"topic": topic, "intent": intent, "sentiment": sent, "severity": sev,
                            "entities": json.loads(ents), "evidence_quote": quote,
                            "needs_review": bool(nr), "label_config": f"{model}+{pv}+enrich-schema-v1"})
                if csrc: rec["cache_source_id"] = csrc
            else:
                rec["reason"] = reason or "empty_review_text"
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        con.close()
    with open("cost/pilot_calls.jsonl", "w") as f:
        for path, phase in ((db_path, "cold"), (warm_path, "warm")):
            con = sqlite3.connect(path)
            where = "WHERE run_id LIKE 'pilot-warm%'" if phase == "warm" else ""
            for row in con.execute("""SELECT call_id, ts, run_id, stage, model, effort, prompt_version,
                    schema_version, batch_size, worker, input_ids, status, attempts,
                    input_tokens, cached_input_tokens, output_tokens, cost_usd, error FROM calls """ + where + " ORDER BY call_id"):
                (cid, ts, rn, stage, model, effort, pv, sv, bs, w, ids, status, att, it, ct, ot, cost, err) = row
                rec = {"request_id": f"{phase}-call-{cid}", "ts": ts, "run_id": rn, "phase": phase,
                       "role": {"classify": "enrich"}.get(stage, stage), "model": model, "effort": effort,
                       "prompt_version": pv, "schema_version": sv, "batch_size": bs, "worker": w,
                       "review_ids": json.loads(ids) if ids else [], "outcome": "succeeded" if status == "ok" else "failed",
                       "attempts": att, "input_tokens": it, "cached_input_tokens": ct,
                       "output_tokens": ot, "cost_usd": cost, "error": err}
                f.write(json.dumps(rec) + "\n")
            con.close()

if __name__ == "__main__":
    run_pilot()

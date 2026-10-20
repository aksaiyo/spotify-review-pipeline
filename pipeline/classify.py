"""Stage 2 - Classify: batched enrichment with exact-text cache, spend cap, resume.
Bounded concurrency shares one spend ledger with worst-case reservations.
Every attempted call is ledgered. Results saved per batch. Transient failures stay pending.
"""
import json, os, sys, time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from db import DB
from labels import LABEL_CONFIG_VERSION, QUARANTINE_EMPTY
from schema import validate_record, normalize_record
from llm_openai import respond, load_rates, cost_of, LLMError, RateLimited, InvalidOutput

PROMPT_VERSION = "enrich-v1"
SCHEMA_VERSION = "enrich-schema-v1"

ENRICH_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"results": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "review_id": {"type": "string"},
            "topic": {"type": "string", "enum": ["access","usability","playback","downloads","catalog","billing","support","other"]},
            "intent": {"type": "string", "enum": ["cancellation","complaint","request","praise","unclear"]},
            "severity": {"type": "integer"},
            "sentiment": {"type": "number"},
            "entities": {"type": "array", "items": {"type": "string"}},
            "needs_review": {"type": "boolean"},
            "evidence_quote": {"type": "string"},
        },
        "required": ["review_id","topic","intent","severity","sentiment","entities","needs_review","evidence_quote"],
    }}},
    "required": ["results"],
}

class SpendCapExceeded(Exception): pass

def cache_key(text_sha, model, effort):
    import hashlib
    return hashlib.sha256(f"{text_sha}|{model}|{effort}|{PROMPT_VERSION}|{SCHEMA_VERSION}".encode()).hexdigest()

EST_PER_BATCH = 0.005

class Budget:
    def __init__(self, cap, spent):
        self.cap, self.spent, self.reserved = cap, spent, 0.0
        self.lock = threading.Lock()
    def admit(self, est):
        with self.lock:
            if self.spent + self.reserved + est > self.cap: return False
            self.reserved += est; return True
    def settle(self, est, actual):
        with self.lock:
            self.reserved -= est; self.spent += actual or 0.0

def sanitize(r, text):
    """Drop unsupported entities; ensure an exact-substring evidence quote; mark fixed records."""
    fixed = False
    low = text.lower()
    ents = [e for e in (r.get("entities") or []) if isinstance(e, str) and e.lower() in low]
    if ents != (r.get("entities") or []):
        r["entities"] = ents; fixed = True
    q = r.get("evidence_quote") or ""
    if not q or q not in text:
        r["evidence_quote"] = text[:150]; fixed = True
    if fixed:
        r["needs_review"] = True
    return r

def _batch_prompt(chunk):
    return "Classify these reviews:\n" + "\n".join(
        f'<review id="{rid}">\n{text}\n</review>' for (rid, text, sh), th, m in chunk)

def _save_chunk_results(db, parsed, chunk, model, effort):
    results = {r["review_id"]: r for r in parsed.get("results", [])}
    done = fail = served = 0
    for (rid, text, sh), th, members in chunk:
        r = results.get(rid)
        if r is None:
            db.execute("UPDATE source_reviews SET attempts=attempts+1 WHERE review_id=?", (rid,)); fail += 1; continue
        r = sanitize(r, text)
        errs = validate_record({**r, "source_sha256": sh, "label_config": LABEL_CONFIG_VERSION}, text, rid)
        if errs:
            quarantine(db, rid, "validation:" + ";".join(errs)[:150]); fail += 1; continue
        rec = normalize_record({**r, "source_sha256": sh})
        payload = dict(rec); payload.pop("review_id"); payload.pop("source_sha256")
        db.execute("INSERT OR REPLACE INTO text_cache VALUES(?,?,?,?,?)",
                   (cache_key(th, model, effort), th, rid, json.dumps(payload), time.time()))
        save_completed(db, rec, text, model)
        done += 1
        for mrid, mtext, msh in members[1:]:
            mrec = dict(rec); mrec["review_id"] = mrid; mrec["source_sha256"] = msh
            mrec["cache_source_id"] = rid
            save_completed(db, mrec, mtext, model)
            served += 1; done += 1
    return done, fail, served

def process_chunk(chunk, db_path, model, effort, api_key, rates, run_id, budget, worker):
    db = DB(db_path)
    sys_prompt = open("prompts/enrich_v1.md").read()
    cost_total = 0.0
    try:
        user = _batch_prompt(chunk)
        parsed = None
        try:
            parsed, usage = respond(sys_prompt, user, model, effort, ENRICH_SCHEMA, api_key, max_output_tokens=8000)
            cost = cost_of(model, usage, rates); cost_total += cost or 0
            ledger_call(db, run_id, "classify", model, effort, chunk, "ok", 1, usage, cost, None, worker)
            if cost: db.execute("INSERT INTO spend_ledger VALUES(?,?,?,?)", (time.time(), run_id, cost, "classify batch"))
        except InvalidOutput as e:
            ledger_call(db, run_id, "classify", model, effort, chunk, "invalid", 1, None, None, str(e)[:500], worker)
            try:
                parsed, usage = respond(sys_prompt, user, model, effort, ENRICH_SCHEMA, api_key, max_output_tokens=8000)
                cost = cost_of(model, usage, rates); cost_total += cost or 0
                ledger_call(db, run_id, "classify", model, effort, chunk, "ok", 2, usage, cost, None, worker)
                if cost: db.execute("INSERT INTO spend_ledger VALUES(?,?,?,?)", (time.time(), run_id, cost, "classify retry"))
            except Exception as e2:
                ledger_call(db, run_id, "classify", model, effort, chunk, "invalid", 2, None, None, str(e2)[:500], worker)
                for (rid, text, sh), th, m in chunk:
                    db.execute("UPDATE source_reviews SET attempts=attempts+1 WHERE review_id=?", (rid,))
                print("left pending for resume: invalid_output_after_retry", flush=True)
                return 0, len(chunk), 0, cost_total
        except RateLimited as e:
            ledger_call(db, run_id, "classify", model, effort, chunk, "rate_limited", 1, None, None, str(e)[:500], worker)
            print("rate limited; backing off 30s and retrying batch once", flush=True)
            time.sleep(30)
            try:
                parsed, usage = respond(sys_prompt, user, model, effort, ENRICH_SCHEMA, api_key, max_output_tokens=8000)
                cost = cost_of(model, usage, rates); cost_total += cost or 0
                ledger_call(db, run_id, "classify", model, effort, chunk, "ok", 2, usage, cost, None, worker)
                if cost: db.execute("INSERT INTO spend_ledger VALUES(?,?,?,?)", (time.time(), run_id, cost, "classify ratelimit retry"))
            except Exception as e2:
                ledger_call(db, run_id, "classify", model, effort, chunk, "error", 2, None, None, str(e2)[:500], worker)
                for (rid, text, sh), th, m in chunk:
                    db.execute("UPDATE source_reviews SET attempts=attempts+1 WHERE review_id=?", (rid,))
                print("left pending for resume: rate_limited_after_retry", flush=True)
                return 0, len(chunk), 0, cost_total
        except LLMError as e:
            ledger_call(db, run_id, "classify", model, effort, chunk, "error", 1, None, None, str(e)[:500], worker)
            for (rid, text, sh), th, m in chunk:
                db.execute("UPDATE source_reviews SET attempts=attempts+1 WHERE review_id=?", (rid,))
            print(f"left pending for resume: {str(e)[:120]}", flush=True)
            return 0, len(chunk), 0, cost_total
        except Exception as e:
            ledger_call(db, run_id, "classify", model, effort, chunk, "error", 1, None, None,
                        f"unexpected {type(e).__name__}: {str(e)[:450]}", worker)
            for (rid, text, sh), th, m in chunk:
                db.execute("UPDATE source_reviews SET attempts=attempts+1 WHERE review_id=?", (rid,))
            print(f"left pending for resume: unexpected {type(e).__name__}: {str(e)[:120]}", flush=True)
            return 0, len(chunk), 0, cost_total
        if parsed is None:
            return 0, len(chunk), 0, cost_total
        d, f, sv = _save_chunk_results(db, parsed, chunk, model, effort)
        return d, f, sv, cost_total
    finally:
        budget.settle(EST_PER_BATCH, cost_total)

def run(db_path, model, effort, api_key, rates_path, run_id,
        batch_size=50, spend_cap=10.0, limit=None, id_prefix=None, only_ids=None, workers=1):
    db = DB(db_path)
    rates = load_rates(rates_path)
    spent0 = db.execute("SELECT COALESCE(SUM(amount_usd),0) FROM spend_ledger").fetchone()[0]
    budget = Budget(spend_cap, spent0)
    q = """SELECT s.review_id, s.review_text, s.text_sha256, s.source_sha256 FROM source_reviews s
           LEFT JOIN enriched e ON e.review_id = s.review_id
           WHERE s.status='pending' AND e.review_id IS NULL"""
    if only_ids is not None:
        marks = ",".join("?" * len(only_ids)); q += f" AND s.review_id IN ({marks})"
    elif id_prefix:
        q += " AND s.review_id LIKE ?"
    q += " ORDER BY (s.cache_source_id IS NOT NULL), s.review_id"
    args = list(only_ids) if only_ids is not None else ([id_prefix + "%"] if id_prefix else [])
    rows = db.execute(q, args).fetchall()
    if limit: rows = rows[:limit]
    print(f"classify: {len(rows)} pending reviews to process, spent_so_far=${spent0:.4f}, workers={workers}", flush=True)
    by_text = {}
    for rid, text, th, sh in rows:
        by_text.setdefault(th, []).append((rid, text, sh))
    todo, n_cache = [], 0
    for th, members in by_text.items():
        ck = cache_key(th, model, effort)
        hit = db.execute("SELECT first_review_id, payload FROM text_cache WHERE cache_key=?", (ck,)).fetchone()
        if hit:
            first_id, payload = hit
            payload = json.loads(payload)
            for rid, text, sh in members:
                rec = dict(payload); rec["review_id"] = rid; rec["source_sha256"] = sh
                rec["cache_source_id"] = first_id
                save_completed(db, rec, text, model)
                n_cache += 1
        else:
            todo.append((th, members))
    print(f"cache hits: {n_cache}; distinct texts to send: {len(todo)}", flush=True)
    units = [(m[0], th, m) for th, m in todo]
    units.sort(key=lambda u: u[0][0])
    chunks = [units[i:i + batch_size] for i in range(0, len(units), batch_size)]
    n_done = n_fail = 0
    t_start = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending_futs = []
        exhausted = False
        for ci, chunk in enumerate(chunks):
            if not budget.admit(EST_PER_BATCH):
                print(f"SPEND CAP would be exceeded (spent=${budget.spent:.4f} + reserved=${budget.reserved:.4f}); "
                      f"stopping admission at chunk {ci}/{len(chunks)} with saved progress", flush=True)
                exhausted = True
                break
            pending_futs.append(pool.submit(process_chunk, chunk, db_path, model, effort,
                                            api_key, rates, run_id, budget, len(pending_futs) % max(1, workers) + 1))
            if len(pending_futs) >= workers * 4:
                for fut in as_completed(list(pending_futs)):
                    d, f, sv, _ = fut.result()
                    n_done += d; n_fail += f; n_cache += sv
                    pending_futs.remove(fut)
                    if (n_done + n_fail) % (batch_size * 20) < batch_size:
                        rate = (n_done + n_fail) / max(1, time.time() - t_start)
                        print(f"  progress: done={n_done} fail={n_fail} cache={n_cache} "
                              f"spent=${budget.spent:.4f} rate={rate:.1f}/s", flush=True)
                        db.checkpoint(f"classify_{run_id}")
        for fut in as_completed(pending_futs):
            d, f, sv, _ = fut.result()
            n_done += d; n_fail += f; n_cache += sv
    db.checkpoint(f"classify_{run_id}_end")
    print(f"classify finished: completed={n_done} failed={n_fail} cache_served={n_cache} "
          f"total_spent=${budget.spent:.4f} elapsed={time.time()-t_start:.0f}s", flush=True)
    if exhausted:
        raise SpendCapExceeded()

def save_completed(db, rec, text, model):
    db.execute("INSERT OR REPLACE INTO enriched VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (rec["review_id"], rec["source_sha256"], "completed", rec["topic"], rec["intent"],
         rec["sentiment"], rec["severity"], json.dumps(rec["entities"], ensure_ascii=False),
         rec["evidence_quote"], int(rec["needs_review"]), LABEL_CONFIG_VERSION,
         model, PROMPT_VERSION, rec.get("cache_source_id"), time.time()))
    db.execute("UPDATE source_reviews SET status='completed', attempts=attempts+1 WHERE review_id=?", (rec["review_id"],))

def quarantine(db, rid, reason):
    db.execute("UPDATE source_reviews SET status='quarantined', quarantine_reason=?, attempts=attempts+1 WHERE review_id=?",
               (reason, rid))

def ledger_call(db, run_id, stage, model, effort, chunk, status, attempts, usage, cost, error, worker=1):
    db.execute("""INSERT INTO calls(ts,run_id,stage,provider,model,effort,prompt_version,schema_version,
        batch_size,worker,input_ids,status,attempts,input_tokens,cached_input_tokens,output_tokens,cost_usd,error)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (time.time(), run_id, stage, "openai", model, effort, PROMPT_VERSION, SCHEMA_VERSION,
         len(chunk), worker, json.dumps([c[0][0] for c in chunk]), status, attempts,
         (usage or {}).get("input_tokens"), ((usage or {}).get("input_tokens_details") or {}).get("cached_tokens"),
         (usage or {}).get("output_tokens"), cost, error))

if __name__ == "__main__":
    # args: db model effort run_id [limit] [spend_cap] [workers]
    db_p, model, effort, run_id = sys.argv[1:5]
    limit = int(sys.argv[5]) if len(sys.argv) > 5 else None
    cap = float(sys.argv[6]) if len(sys.argv) > 6 else 10.0
    w = int(sys.argv[7]) if len(sys.argv) > 7 else 1
    run(db_p, model, effort, os.environ["OPENAI_API_KEY"], "cost/rates.csv", run_id,
        spend_cap=cap, limit=limit, workers=w)

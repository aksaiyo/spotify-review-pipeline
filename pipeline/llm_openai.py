"""Minimal OpenAI Responses-API client over httpx. No SDK dependency.
Bounded retries with backoff+jitter, raw usage capture, cost computation from editable rates.
Salvage: keeps the first complete JSON object when review text tricks the model into
emitting trailing garbage after it.
"""
import json, os, random, time
import httpx

API = "https://api.openai.com/v1/responses"


def _post_hard_deadline(headers, body, timeout, hard_deadline):
    """Run the POST in a daemon thread with an absolute wall-clock deadline.
    httpx timeouts bound connect/read/write individually but a trickling or
    black-holed connection can evade them; this cannot run past hard_deadline.
    An abandoned thread just leaks one dying socket; callers retry on a fresh client."""
    import queue, threading
    q = queue.Queue()
    def work():
        try:
            with httpx.Client(timeout=timeout) as c:
                q.put(("ok", c.post(API, headers=headers, json=body)))
        except Exception as e:
            q.put(("err", e))
    threading.Thread(target=work, daemon=True).start()
    import time as _t
    t0 = _t.time()
    while True:
        try:
            kind, val = q.get(timeout=3)
            break
        except queue.Empty:
            # wall-clock check: monotonic timers pause while the sandbox VM is suspended
            if _t.time() - t0 > hard_deadline:
                raise httpx.TimeoutException(f"hard deadline {hard_deadline}s exceeded")
    if kind == "err":
        raise val
    return val

class LLMError(Exception): pass
class RateLimited(LLMError): pass
class InvalidOutput(LLMError): pass

def load_rates(path):
    rates = {}
    import csv
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            rates[r["model"]] = {"input": float(r["input_per_1m"]), "cached": float(r["cached_per_1m"]),
                                 "output": float(r["output_per_1m"]), "dated": r.get("dated", "")}
    return rates

def cost_of(model, usage, rates):
    r = rates.get(model)
    if not r or not usage: return None
    it = usage.get("input_tokens", 0) or 0
    ct = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0) or 0
    ot = usage.get("output_tokens", 0) or 0
    return round(((it - ct) * r["input"] + ct * r["cached"] + ot * r["output"]) / 1_000_000, 6)

def respond(prompt_sys, prompt_user, model, effort, schema, api_key, timeout=180, max_output_tokens=4000):
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "reasoning": {"effort": effort},
        "input": [
            {"role": "system", "content": prompt_sys},
            {"role": "user", "content": prompt_user},
        ],
        "text": {"format": {"type": "json_schema", "name": "enrich", "schema": schema, "strict": True}},
        "max_output_tokens": max_output_tokens,
        "store": False,
    }
    back = 2.0
    for attempt in (1, 2, 3):
        try:
            r = _post_hard_deadline(headers, body, timeout, hard_deadline=150)
            if r.status_code == 429:
                raise RateLimited(r.text[:300])
            if r.status_code >= 500:
                raise LLMError(f"server {r.status_code}: {r.text[:200]}")
            if r.status_code >= 400:
                raise LLMError(f"client {r.status_code}: {r.text[:400]}")
            data = r.json()
        except (httpx.TimeoutException, httpx.TransportError) as e:
            if attempt == 3: raise LLMError(f"network: {e}")
            time.sleep(back + random.random()); back *= 2; continue
        status = data.get("status")
        if status != "completed":
            reason = (data.get("incomplete_details") or {}).get("reason", "")
            if status == "incomplete" and reason == "max_output_tokens":
                raise InvalidOutput(f"truncated at max_output_tokens: {json.dumps(data.get('usage'))}")
            if attempt == 3: raise LLMError(f"status={status}: {json.dumps(data)[:300]}")
            time.sleep(back + random.random()); back *= 2; continue
        out_text = ""
        for item in data.get("output", []):
            if item.get("type") == "message":
                for part in item.get("content", []):
                    if part.get("type") == "output_text":
                        out_text += part.get("text", "")
        try:
            parsed = json.loads(out_text)
        except json.JSONDecodeError as je:
            try:
                dec = json.JSONDecoder()
                lead = out_text.lstrip()
                obj, end = dec.raw_decode(lead)
                if isinstance(obj, dict) and lead[end:].strip():
                    print(f"salvaged first JSON object; ignored {len(lead)-end} trailing chars", flush=True)
                    parsed = obj
                else:
                    raise InvalidOutput(f"json error at pos {je.pos}: {je.msg}")
            except InvalidOutput:
                raise
            except Exception:
                raise InvalidOutput(f"json error at pos {je.pos}: {je.msg} | tail: {out_text[max(0,je.pos-120):je.pos+80]}")
        return parsed, data.get("usage") or {}
    raise LLMError("unreachable")

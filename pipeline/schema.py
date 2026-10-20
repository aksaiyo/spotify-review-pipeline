"""Row hashing + record validation/normalization against the common labels."""
import hashlib, json
from labels import TOPIC_ORDER, INTENTS, LABEL_CONFIG_VERSION

def sha256_text(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def row_sha(row: dict) -> str:
    """Contract hashing: sha256 of canonical JSON of the raw row values."""
    return hashlib.sha256(canonical(row).encode("utf-8")).hexdigest()

def validate_record(rec: dict, source_text: str, source_id) -> list:
    errs = []
    if rec.get("review_id") != source_id:
        errs.append("review_id mismatch")
    if rec.get("topic") not in TOPIC_ORDER:
        errs.append(f"bad topic: {rec.get('topic')}")
    if rec.get("intent") not in INTENTS:
        errs.append(f"bad intent: {rec.get('intent')}")
    sev = rec.get("severity")
    if not isinstance(sev, int) or not (1 <= sev <= 5):
        errs.append(f"bad severity: {sev}")
    s = rec.get("sentiment")
    if not isinstance(s, (int, float)) or not (-1.0 <= float(s) <= 1.0):
        errs.append(f"bad sentiment: {s}")
    q = rec.get("evidence_quote")
    if not isinstance(q, str) or not q:
        errs.append("missing evidence_quote")
    elif q not in source_text:
        errs.append("evidence_quote not an exact source substring")
    ents = rec.get("entities")
    if not isinstance(ents, list) or not all(isinstance(e, str) for e in ents):
        errs.append("entities must be a list of strings")
    else:
        low = source_text.lower()
        bad = [e for e in ents if e.lower() not in low]
        if bad:
            errs.append(f"unsupported entities: {bad}")
    if not isinstance(rec.get("needs_review"), bool):
        errs.append("needs_review must be boolean")
    if rec.get("label_config") != LABEL_CONFIG_VERSION:
        errs.append("label_config mismatch")
    return errs

def normalize_record(rec: dict) -> dict:
    out = dict(rec)
    out["severity"] = int(out["severity"])
    out["sentiment"] = round(float(out["sentiment"]), 3)
    out["needs_review"] = bool(out["needs_review"])
    out["entities"] = sorted({e.strip() for e in out["entities"] if e.strip()})
    out["label_config"] = LABEL_CONFIG_VERSION
    return out

def to_jsonl(rec: dict) -> str:
    return json.dumps(rec, ensure_ascii=False, sort_keys=True)

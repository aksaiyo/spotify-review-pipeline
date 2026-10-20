"""Cost/runtime calculator (COST_CALCULATOR.md). Default: OFFLINE replay of saved pilot calls.
Paid execution is a separate explicit command (pipeline/pilot.py).
Usage: python3 cost/calculator.py            # offline replay + projections
       python3 cost/calculator.py report     # write cost/report.md
"""
import json, os, sys, time

RATES_PATH = os.path.join(os.path.dirname(__file__), "rates.csv")
PILOT_CALLS = os.path.join(os.path.dirname(__file__), "pilot_calls.jsonl")
PILOT_TIMING = os.path.join(os.path.dirname(__file__), "pilot_timing.json")

def load_rates():
    import csv
    rates = {}
    with open(RATES_PATH, newline="") as f:
        for r in csv.DictReader(f):
            rates[r["model"]] = {"input_per_1m": float(r["input_per_1m"]), "cached_per_1m": float(r["cached_per_1m"]),
                                 "output_per_1m": float(r["output_per_1m"]), "dated": r.get("dated", ""),
                                 "source": "https://developers.openai.com/api/docs/models/gpt-6-luna"}
    return rates

def replay(_=None):
    rates = load_rates()
    calls = []
    if os.path.exists(PILOT_CALLS):
        for line in open(PILOT_CALLS):
            line = line.strip()
            if line: calls.append(json.loads(line))
    by_stage = {}
    total = 0.0
    unknown = 0
    for c in calls:
        st = by_stage.setdefault(c.get("stage") or c.get("role", "?"), {"calls": 0, "cost": 0.0, "input_tokens": 0, "output_tokens": 0})
        st["calls"] += 1
        cost = c.get("cost_usd")
        if cost is None and c.get("input_tokens") is not None and c.get("model") in rates:
            r = rates[c["model"]]
            cost = ((c.get("input_tokens", 0) - (c.get("cached_input_tokens") or 0)) * r["input_per_1m"]
                    + (c.get("cached_input_tokens") or 0) * r["cached_per_1m"]
                    + c.get("output_tokens", 0) * r["output_per_1m"]) / 1_000_000
        if cost is None: unknown += 1
        else:
            st["cost"] += cost; total += cost
        st["input_tokens"] += c.get("input_tokens") or 0
        st["output_tokens"] += c.get("output_tokens") or 0
    return {"mode": "offline_replay", "calls": len(calls), "unknown_usage_calls": unknown,
            "total_cost_usd": round(total, 6), "by_stage": by_stage,
            "rates_used": rates, "recomputed_at": time.time()}

def projections(replay_out):
    en = replay_out["by_stage"].get("enrich", {})
    if not os.path.exists(PILOT_CALLS) or not en.get("calls"):
        return {"note": "no pilot data yet"}
    cold_calls = [c for c in (json.loads(l) for l in open(PILOT_CALLS) if l.strip())
                  if (c.get("stage") or c.get("role")) == "enrich" and c.get("phase") == "cold" and c.get("cost_usd")]
    cost_cold = sum(c["cost_usd"] for c in cold_calls)
    n_cold = sum(len(c.get("review_ids", [])) for c in cold_calls)
    per = cost_cold / n_cold if n_cold else None
    scenarios = {}
    for label, distinct in (("100k_floor_73300_distinct", 73300), ("full_corpus_484189_distinct", 484189)):
        if per:
            scenarios[label] = {"est_classify_cost_usd": round(distinct * per * 1.08, 2),
                                "note": "includes 8% retry allowance; excludes ~$0.03 verify/group/memo"}
    timing = {}
    if os.path.exists(PILOT_TIMING):
        timing = json.load(open(PILOT_TIMING))
    return {"measured_per_review_usd": round(per, 7) if per else None,
            "cold_reviews_measured": n_cold, "pilot_timing": timing, "scenarios": scenarios}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "replay"
    r = replay()
    out = dict(r); out["projections"] = projections(r)
    txt = json.dumps(out, indent=2)
    if cmd == "report":
        with open(os.path.join(os.path.dirname(__file__), "report.md"), "w") as f:
            f.write("# Cost/runtime report (offline replay of saved pilot calls)\n\n```json\n" + txt + "\n```\n")
        print("wrote cost/report.md")
    print(txt)

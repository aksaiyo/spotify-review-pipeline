# Cost/runtime report (offline replay of saved pilot calls)

```json
{
  "mode": "offline_replay",
  "calls": 4,
  "unknown_usage_calls": 0,
  "total_cost_usd": 0.00625,
  "by_stage": {
    "enrich": {
      "calls": 2,
      "cost": 0.004326,
      "input_tokens": 6678,
      "output_tokens": 7317
    },
    "verify": {
      "calls": 1,
      "cost": 0.001631,
      "input_tokens": 1840,
      "output_tokens": 2893
    },
    "group": {
      "calls": 1,
      "cost": 0.000293,
      "input_tokens": 1445,
      "output_tokens": 298
    }
  },
  "rates_used": {
    "gpt-6-luna": {
      "input_per_1m": 0.1,
      "cached_per_1m": 0.01,
      "output_per_1m": 0.5,
      "dated": "2026-10-19",
      "source": "https://developers.openai.com/api/docs/models/gpt-6-luna"
    }
  },
  "recomputed_at": 1792526882.1498013,
  "projections": {
    "measured_per_review_usd": 4.33e-05,
    "cold_reviews_measured": 100,
    "pilot_timing": {
      "ingest_s": 0.06,
      "classify_cold_s": 47.34,
      "verify_s": 22.8,
      "group_rank_s": 4.36,
      "classify_warm_s": 0.13,
      "warm_new_enrichment_calls": 0
    },
    "scenarios": {
      "100k_floor_73300_distinct": {
        "est_classify_cost_usd": 3.42,
        "note": "includes 8% retry allowance; excludes ~$0.03 verify/group/memo"
      },
      "full_corpus_484189_distinct": {
        "est_classify_cost_usd": 22.62,
        "note": "includes 8% retry allowance; excludes ~$0.03 verify/group/memo"
      }
    }
  }
}
```

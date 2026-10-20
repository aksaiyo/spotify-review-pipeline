# Cost / runtime calculator (100-review pilot)

Offline replay (default, no network, no key needed):

    python3 cost/calculator.py            # replays cost/pilot_calls.jsonl with cost/rates.csv
    python3 cost/calculator.py report     # same, also writes cost/report.md

Explicit paid pilot (needs OPENAI_API_KEY in your environment; spends about $0.006):

    python3 pipeline/pilot.py

Files
- `rates.csv`: dated, editable per-million-token rates (source: https://developers.openai.com/api/docs/models/gpt-6-luna).
- `pilot_calls.jsonl`: real per-attempt usage and timing for the cold and warm pilot runs.
- `usage.csv`: one row per charged call, cost recomputed from rates.csv; `maps_to_pilot_calls_line` is the line in pilot_calls.jsonl.
- `pilot_records.jsonl`, `pilot_timing.json`: per-ID pilot results and measured time.
- `report.md`: measured-100 table and full-run estimates.
Doubling every rate in rates.csv doubles the API subtotal; local compute and measured time do not change.

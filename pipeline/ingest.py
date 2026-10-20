"""Stage 1 - Prepare: ingest the full CSV, profile, dedup, quarantine empties, queue pending.
No model calls. Multiline-safe CSV parsing. Outputs ingestion report + manifest.
"""
import csv, hashlib, json, sys, time
from collections import Counter
from pathlib import Path
from db import DB
from schema import sha256_text, row_sha

csv.field_size_limit(1024 * 1024 * 16)

EXPECTED = {
    "rows": 660_622, "empty_texts": 13, "missing_app_version": 159_701,
    "distinct_nonempty_texts": 484_189, "file_bytes": 97_400_616,
}

def file_sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()

def ingest(csv_path, db_path, out_dir):
    t0 = time.time()
    csv_path = Path(csv_path); out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    db = DB(db_path)
    fhash = file_sha256(csv_path)
    nbytes = csv_path.stat().st_size
    n_rows = n_empty = n_missing_version = 0
    dup_ids = Counter(); ratings = Counter(); years = Counter()
    text_counts = Counter()
    batch = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        cols = reader.fieldnames
        for row in reader:
            n_rows += 1
            rid = (row.get("review_id") or "").strip()
            text = row.get("review_text") or ""
            rating = row.get("review_rating") or ""
            likes = row.get("review_likes") or ""
            ver = (row.get("app_version") or "").strip()
            ts = (row.get("review_timestamp") or "").strip()
            dup_ids[rid] += 1
            if rating: ratings[rating] += 1
            if len(ts) >= 4: years[ts[:7]] += 1
            if not ver: n_missing_version += 1
            empty = text.strip() == ""
            if empty: n_empty += 1
            else: text_counts[text] += 1
            raw = {"review_id": row.get("review_id") or "", "review_text": text,
                   "review_rating": row.get("review_rating") or "", "review_likes": row.get("review_likes") or "",
                   "app_version": row.get("app_version") or "", "review_timestamp": row.get("review_timestamp") or ""}
            batch.append((rid, text, int(rating) if rating.isdigit() else None,
                          int(likes) if likes.isdigit() else None, ver or None, ts or None,
                          row_sha(raw), sha256_text(text.strip().lower()),
                          "quarantined" if empty else "pending",
                          "empty_review_text" if empty else None, 0, None))
            if len(batch) >= 5000:
                db.executemany("INSERT OR REPLACE INTO source_reviews VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", batch)
                batch.clear()
    if batch:
        db.executemany("INSERT OR REPLACE INTO source_reviews VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", batch)
    distinct_raw = db.execute(
        "SELECT COUNT(DISTINCT text_sha256) FROM source_reviews WHERE status='pending'").fetchone()[0]
    db.execute("""CREATE TEMP TABLE cache_map AS
        SELECT text_sha256 k, MIN(review_id) rid
        FROM source_reviews WHERE status='pending' GROUP BY text_sha256""")
    db.execute("CREATE INDEX idx_cache_map_k ON cache_map(k)")
    db.execute("""UPDATE source_reviews SET cache_source_id =
        (SELECT rid FROM cache_map WHERE cache_map.k = source_reviews.text_sha256)
        WHERE status='pending'""")
    db.execute("UPDATE source_reviews SET cache_source_id=NULL WHERE review_id=cache_source_id")
    distinct_nonempty = len(text_counts)
    exact_dup_texts = sum(c - 1 for c in text_counts.values() if c > 1)
    report = {
        "file": str(csv_path), "file_bytes": nbytes, "file_sha256": fhash,
        "columns": cols, "rows": n_rows,
        "empty_texts_quarantined": n_empty,
        "missing_app_version": n_missing_version,
        "duplicate_review_ids": {k: v for k, v in dup_ids.items() if v > 1},
        "distinct_nonempty_texts": distinct_nonempty,
        "distinct_nonempty_raw_sql": distinct_raw,
        "dedup_basis": "exact",
        "extra_rows_from_exact_text_duplicates": exact_dup_texts,
        "rating_distribution": dict(sorted(ratings.items())),
        "month_buckets_top": years.most_common(25),
        "expected_vs_actual": {k: {"expected": v, "actual": a}
            for k, v, a in [("rows", EXPECTED["rows"], n_rows),
                            ("empty_texts", EXPECTED["empty_texts"], n_empty),
                            ("missing_app_version", EXPECTED["missing_app_version"], n_missing_version),
                            ("distinct_nonempty_texts", EXPECTED["distinct_nonempty_texts"], distinct_nonempty),
                            ("file_bytes", EXPECTED["file_bytes"], nbytes)]},
        "elapsed_s": round(time.time() - t0, 2),
    }
    (out_dir / "ingestion_report.json").write_text(json.dumps(report, indent=2))
    manifest = {"source_file": csv_path.name, "sha256": fhash, "bytes": nbytes,
                "ingested_at": t0, "rows": n_rows}
    (out_dir / "data_manifest.json").write_text(json.dumps(manifest, indent=2))
    db.checkpoint("post_ingest")
    print(json.dumps(report["expected_vs_actual"], indent=2), flush=True)
    print(f"rows={n_rows} empty={n_empty} distinct={distinct_nonempty} elapsed={report['elapsed_s']}s", flush=True)

if __name__ == "__main__":
    ingest(sys.argv[1], sys.argv[2], sys.argv[3])

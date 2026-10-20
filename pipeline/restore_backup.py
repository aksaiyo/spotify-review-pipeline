"""Restore model-produced state from a Drive backup after a sandbox rebuild.
Usage: re-ingest the CSV first, then: python3 pipeline/restore_backup.py <backup.jsonl.gz> <db_path>
"""
import gzip, json, sqlite3, sys, time

def restore(backup_path, db_path):
    con = sqlite3.connect(db_path, isolation_level=None)
    counts = {}
    tables = {}
    with gzip.open(backup_path, "rt", encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            t, row = rec["table"], rec["row"]
            counts[t] = counts.get(t, 0) + 1
            if t == "source_status":
                con.execute("""UPDATE source_reviews SET status=?, quarantine_reason=?, attempts=?, cache_source_id=?
                    WHERE review_id=?""", (row["status"], row["quarantine_reason"], row["attempts"],
                                           row["cache_source_id"], row["review_id"]))
                continue
            if t not in tables:
                cols = [c[1] for c in con.execute(f"PRAGMA table_info({t})")]
                tables[t] = cols
            cols = tables[t]
            vals = [row.get(c) for c in cols]
            marks = ",".join("?" * len(cols))
            con.execute(f"INSERT OR REPLACE INTO {t} VALUES({marks})", vals)
    con.close()
    print("restored:", counts)

if __name__ == "__main__":
    restore(sys.argv[1], sys.argv[2])

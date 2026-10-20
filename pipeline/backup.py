"""Backup/restore model-produced state to/from Google Drive (sandbox-death insurance).
Backs up: enriched, calls, text_cache, issues, membership, spend_ledger, source statuses.
Source rows re-ingest from the CSV in ~2.5 min, so they are not backed up.
"""
import gzip, json, os, sqlite3, subprocess, sys, time

TABLES = ["enriched", "calls", "text_cache", "issues", "membership", "spend_ledger"]

def dump_state(db_path, out_path):
    con = sqlite3.connect(db_path)
    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        for t in TABLES:
            try:
                cols = [c[1] for c in con.execute(f"PRAGMA table_info({t})")]
                for row in con.execute(f"SELECT * FROM {t}"):
                    f.write(json.dumps({"table": t, "row": dict(zip(cols, row))}) + "\n")
            except sqlite3.OperationalError:
                pass
        cols = [c[1] for c in con.execute("PRAGMA table_info(source_reviews)")]
        keep = ["review_id", "status", "quarantine_reason", "attempts", "cache_source_id"]
        idx = [cols.index(k) for k in keep]
        for row in con.execute("SELECT review_id, status, quarantine_reason, attempts, cache_source_id FROM source_reviews WHERE status != 'pending'"):
            f.write(json.dumps({"table": "source_status", "row": dict(zip(keep, row))}) + "\n")
    con.close()
    print(f"backup: wrote {out_path} ({os.path.getsize(out_path)//1024} KB)", flush=True)

def upload(out_path, name=None):
    name = name or os.path.basename(out_path)
    r = subprocess.run(["tools", "google-drive", "upload", "--file-path", out_path,
                        "--name", name, "--json"], capture_output=True, text=True, timeout=600)
    if r.returncode == 0:
        try:
            link = json.loads(r.stdout)["file"]["web_view_link"]
            print(f"backup: uploaded {name} -> {link}", flush=True)
            return link
        except Exception:
            pass
    print(f"backup: upload FAILED for {name}: {r.stderr[:200]}", flush=True)
    return None

def backup(db_path, tag):
    out = f"/tmp/mba290t-state-{tag}.jsonl.gz"
    dump_state(db_path, out)
    return upload(out)

if __name__ == "__main__":
    backup(sys.argv[1] if len(sys.argv) > 1 else "runs/pipeline.db",
           sys.argv[2] if len(sys.argv) > 2 else str(int(time.time())))

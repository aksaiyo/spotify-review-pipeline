"""Build the deployable slim SQLite DB from the full pipeline DB (no model calls).
Keeps: enriched labels, membership, issues, source text only for reviews used as issue evidence
or in the top issues, run metadata, and the memo. Source rows that were never classified are
counted in meta (not copied) to keep the file small.
"""
import json, sqlite3, sys, os
src = sys.argv[1] if len(sys.argv) > 1 else "runs/pipeline.db"
out = sys.argv[2] if len(sys.argv) > 2 else "web/dashboard.db"
memo_path = sys.argv[3] if len(sys.argv) > 3 else "runs/memo.md"
if os.path.exists(out): os.remove(out)
s = sqlite3.connect(src); d = sqlite3.connect(out)
for t in ("enriched", "issues", "membership"):
    ddl = s.execute("SELECT sql FROM sqlite_master WHERE name=?", (t,)).fetchone()[0]
    d.execute(ddl)
d.execute("CREATE TABLE source_reviews(review_id TEXT PRIMARY KEY, review_text TEXT, status TEXT, quarantine_reason TEXT)")
d.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
d.execute("CREATE TABLE memo(id INTEGER PRIMARY KEY, body TEXT)")
d.executemany("INSERT INTO enriched SELECT * FROM (SELECT ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [])
cols = [c[1] for c in s.execute("PRAGMA table_info(enriched)")]
rows = s.execute("SELECT * FROM enriched").fetchall()
d.executemany(f"INSERT INTO enriched VALUES({','.join('?'*len(cols))})", rows)
d.executemany("INSERT INTO issues VALUES(?,?,?,?)", s.execute("SELECT issue_id,name,rule,created_at FROM issues").fetchall())
d.executemany("INSERT INTO membership VALUES(?,?)", s.execute("SELECT issue_id,review_id FROM membership").fetchall())
# source text: only the 20 highest-severity member reviews per issue (what the issue page shows)
keep = set()
for (iid,) in s.execute("SELECT DISTINCT issue_id FROM membership"):
    for (rid,) in s.execute("""SELECT m.review_id FROM membership m JOIN enriched e ON e.review_id=m.review_id
        WHERE m.issue_id=? ORDER BY e.severity DESC, m.review_id LIMIT 20""", (iid,)):
        keep.add(rid)
for rid in keep:
    r = s.execute("SELECT review_id, review_text, status, quarantine_reason FROM source_reviews WHERE review_id=?", (rid,)).fetchone()
    d.execute("INSERT INTO source_reviews VALUES(?,?,?,?)", r)
meta = {
  "source_rows": s.execute("SELECT COUNT(*) FROM source_reviews").fetchone()[0],
  "completed": s.execute("SELECT COUNT(*) FROM source_reviews WHERE status='completed'").fetchone()[0],
  "quarantined_empty": s.execute("SELECT COUNT(*) FROM source_reviews WHERE quarantine_reason='empty_review_text'").fetchone()[0],
  "unprocessed": s.execute("SELECT COUNT(*) FROM source_reviews WHERE status='pending'").fetchone()[0],
  "cache_served": s.execute("SELECT COUNT(*) FROM enriched WHERE cache_source_id IS NOT NULL").fetchone()[0],
  "spend_usd": s.execute("SELECT COALESCE(SUM(amount_usd),0) FROM spend_ledger").fetchone()[0],
}
for k, v in meta.items(): d.execute("INSERT INTO meta VALUES(?,?)", (k, json.dumps(v)))
d.execute("INSERT INTO memo VALUES(1,?)", (open(memo_path).read(),))
d.commit(); d.execute("VACUUM"); d.close()
print(meta, os.path.getsize(out)//1024, "KB")

"""SQLite storage: source rows, enriched records, call ledger, cache, issues, checkpoints."""
import json, sqlite3, time
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS source_reviews(
  review_id TEXT PRIMARY KEY,
  review_text TEXT NOT NULL,
  review_rating INTEGER,
  review_likes INTEGER,
  app_version TEXT,
  review_timestamp TEXT,
  source_sha256 TEXT NOT NULL,
  text_sha256 TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  quarantine_reason TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  cache_source_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_source_status ON source_reviews(status);
CREATE INDEX IF NOT EXISTS idx_text_hash ON source_reviews(text_sha256);
CREATE TABLE IF NOT EXISTS enriched(
  review_id TEXT PRIMARY KEY,
  source_sha256 TEXT NOT NULL,
  status TEXT NOT NULL,
  topic TEXT NOT NULL, intent TEXT NOT NULL,
  sentiment REAL NOT NULL, severity INTEGER NOT NULL,
  entities TEXT NOT NULL,
  evidence_quote TEXT NOT NULL,
  needs_review INTEGER NOT NULL,
  label_config TEXT NOT NULL,
  model TEXT, prompt_version TEXT, cache_source_id TEXT,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS calls(
  call_id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL, run_id TEXT NOT NULL, stage TEXT NOT NULL,
  provider TEXT, model TEXT, effort TEXT,
  prompt_version TEXT, schema_version TEXT,
  batch_size INTEGER, worker INTEGER,
  input_ids TEXT,
  status TEXT NOT NULL,
  attempts INTEGER NOT NULL,
  input_tokens INTEGER, cached_input_tokens INTEGER, output_tokens INTEGER,
  cost_usd REAL, error TEXT
);
CREATE TABLE IF NOT EXISTS text_cache(
  cache_key TEXT PRIMARY KEY,
  text_sha256 TEXT NOT NULL,
  first_review_id TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS issues(
  issue_id TEXT PRIMARY KEY,
  name TEXT,
  rule TEXT,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS membership(
  issue_id TEXT NOT NULL,
  review_id TEXT NOT NULL,
  PRIMARY KEY (issue_id, review_id)
);
CREATE TABLE IF NOT EXISTS spend_ledger(
  ts REAL NOT NULL, run_id TEXT, amount_usd REAL NOT NULL, note TEXT
);
CREATE TABLE IF NOT EXISTS checkpoints(
  name TEXT PRIMARY KEY,
  snapshot TEXT NOT NULL,
  created_at REAL NOT NULL
);
"""

class DB:
    def __init__(self, path):
        self.path = Path(path)
        self.conn = sqlite3.connect(str(self.path), isolation_level=None)
        self.conn.executescript(SCHEMA)
        self.conn.execute("PRAGMA busy_timeout=5000")

    def execute(self, *a): return self.conn.execute(*a)
    def executemany(self, *a): return self.conn.executemany(*a)

    def checkpoint(self, name):
        snap = {
            "ts": time.time(),
            "source_counts": dict(self.conn.execute(
                "SELECT status, COUNT(*) FROM source_reviews GROUP BY status").fetchall()),
            "enriched": self.conn.execute("SELECT COUNT(*) FROM enriched").fetchone()[0],
            "calls": self.conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0],
            "spend": self.conn.execute("SELECT COALESCE(SUM(amount_usd),0) FROM spend_ledger").fetchone()[0],
        }
        self.conn.execute("INSERT OR REPLACE INTO checkpoints VALUES(?,?,?)",
                          (name, json.dumps(snap), time.time()))
        return snap

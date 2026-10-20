"""Read-only backend: serves the dashboard API straight from the SQLite pipeline DB.
Stdlib only. Endpoints: /api/metrics /api/issues /api/issue/<id> /api/memo /api/health
"""
import json, os, sqlite3, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

DB_PATH = os.environ.get("PIPELINE_DB", "runs/pipeline.db")
MEMO_PATH = os.environ.get("MEMO_PATH", "runs/memo.md")

def q(sql, args=()):
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(sql, args)]
    con.close(); return rows

def metrics():
    m = {}
    m["source_rows"] = q("SELECT COUNT(*) c FROM source_reviews")[0]["c"]
    m["completed"] = q("SELECT COUNT(*) c FROM source_reviews WHERE status='completed'")[0]["c"]
    m["quarantined"] = q("SELECT COUNT(*) c FROM source_reviews WHERE status='quarantined'")[0]["c"]
    m["quarantine_reasons"] = q("SELECT quarantine_reason reason, COUNT(*) n FROM source_reviews WHERE status='quarantined' GROUP BY 1 ORDER BY 2 DESC")
    m["cache_served"] = q("SELECT COUNT(*) c FROM enriched WHERE cache_source_id IS NOT NULL")[0]["c"]
    m["spend_usd"] = q("SELECT COALESCE(SUM(amount_usd),0) s FROM spend_ledger")[0]["s"]
    m["intent_breakdown"] = q("SELECT intent, COUNT(*) n FROM enriched WHERE status='completed' GROUP BY 1 ORDER BY 2 DESC")
    m["topic_breakdown"] = q("SELECT topic, COUNT(*) n FROM enriched WHERE status='completed' GROUP BY 1 ORDER BY 2 DESC")
    m["severity_hist"] = q("SELECT severity, COUNT(*) n FROM enriched WHERE status='completed' GROUP BY 1 ORDER BY 1")
    return m

def issues():
    return q("""SELECT m.issue_id, i.name, COUNT(*) complaint_count, SUM(e.severity) severity_sum,
        ROUND(AVG(CAST(e.severity AS REAL)),6) mean_severity, SUM(e.severity) priority_score
        FROM membership m JOIN enriched e ON e.review_id=m.review_id
        LEFT JOIN issues i ON i.issue_id=m.issue_id
        WHERE e.intent IN ('complaint','cancellation')
        GROUP BY m.issue_id ORDER BY priority_score DESC, m.issue_id ASC""")

def issue_detail(iid):
    return {"issue_id": iid, "samples": q("""SELECT m.review_id, s.review_text, e.severity, e.evidence_quote
        FROM membership m JOIN source_reviews s ON s.review_id=m.review_id
        JOIN enriched e ON e.review_id=m.review_id
        WHERE m.issue_id=? ORDER BY e.severity DESC, m.review_id LIMIT 20""", (iid,))}

class H(BaseHTTPRequestHandler):
    def _send(self, obj, code=200, ctype="application/json"):
        body = obj.encode() if isinstance(obj, str) else json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        p = urlparse(self.path).path
        try:
            if p == "/api/health": self._send({"ok": True})
            elif p == "/api/metrics": self._send(metrics())
            elif p == "/api/issues": self._send(issues())
            elif p.startswith("/api/issue/"): self._send(issue_detail(p.rsplit("/", 1)[1]))
            elif p == "/api/memo":
                self._send(open(MEMO_PATH).read() if os.path.exists(MEMO_PATH) else "memo not generated yet", ctype="text/markdown")
            elif p in ("/", "/index.html"):
                self._send(open(os.path.join(os.path.dirname(__file__), "dashboard.html")).read(), ctype="text/html")
            else: self._send({"error": "not found"}, 404)
        except Exception as e:
            self._send({"error": str(e)}, 500)
    def log_message(self, *a): pass

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    print(f"serving on :{port} from {DB_PATH}")
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()

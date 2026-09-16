"""Local transcript log. Useful for spotting recurring mis-transcriptions
worth adding to `vocabulary`, and for recovering something you dictated into
the wrong window."""
import sqlite3
import threading
import time

from .config import HISTORY_PATH

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS dictations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  app TEXT,
  mode TEXT,
  seconds REAL,
  latency_ms INTEGER,
  raw TEXT,
  final TEXT
);
CREATE INDEX IF NOT EXISTS idx_ts ON dictations(ts DESC);
"""


def _conn():
    c = sqlite3.connect(HISTORY_PATH, timeout=5)
    c.executescript(SCHEMA)
    return c


def log(app, mode, seconds, latency_ms, raw, final):
    try:
        with _lock, _conn() as c:
            c.execute(
                "INSERT INTO dictations (ts, app, mode, seconds, latency_ms, raw, final)"
                " VALUES (?,?,?,?,?,?,?)",
                (time.time(), app, mode, seconds, latency_ms, raw, final),
            )
    except Exception:
        pass


def recent(n=15):
    try:
        with _lock, _conn() as c:
            return c.execute(
                "SELECT ts, app, final FROM dictations ORDER BY ts DESC LIMIT ?", (n,)
            ).fetchall()
    except Exception:
        return []


def stats():
    try:
        with _lock, _conn() as c:
            row = c.execute(
                "SELECT COUNT(*), COALESCE(SUM(seconds),0), COALESCE(AVG(latency_ms),0),"
                " COALESCE(SUM(LENGTH(final)),0) FROM dictations"
            ).fetchone()
        count, secs, avg_ms, chars = row
        # ~200 wpm spoken vs ~50 wpm typed for most people
        words = chars / 5.0
        typed_minutes = words / 50.0
        spoken_minutes = secs / 60.0
        return {
            "dictations": count,
            "words": int(words),
            "avg_latency_ms": int(avg_ms),
            "minutes_saved": max(0.0, typed_minutes - spoken_minutes),
        }
    except Exception:
        return {"dictations": 0, "words": 0, "avg_latency_ms": 0, "minutes_saved": 0.0}

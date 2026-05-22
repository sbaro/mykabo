"""
Pytest configuration — must run before test_backend.py imports main.py.

Sets DB_PATH to a per-session temp file and configures fixed credentials
so tests are fully isolated from any production database.
"""
import os
import sys
import tempfile

# ── Ensure the project root is importable ────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Isolated temp database ────────────────────────────────────────────────────
_fd, _TMP_DB = tempfile.mkstemp(suffix="-kanban-test.db")
os.close(_fd)
os.environ["DB_PATH"] = _TMP_DB
os.environ.setdefault("KANBAN_USER", "admin")
os.environ.setdefault("KANBAN_PASS", "changeme")

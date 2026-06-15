from fastapi import FastAPI, HTTPException, Depends, Response, Cookie, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Any
import json
import sqlite3
import os
import secrets
import time
import re
import calendar
from datetime import datetime, timezone, date, timedelta

try:
    from passlib.context import CryptContext
    pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    def hash_pass(p: str) -> str: return pwd_ctx.hash(p)
    def verify_pass(p: str, h: str) -> bool: return pwd_ctx.verify(p, h)
except ImportError:
    # Fallback if passlib not installed — still salted via hmac
    import hashlib
    import hmac
    _SALT = os.environ.get("PASSWORD_SALT", secrets.token_hex(16))
    def hash_pass(p: str) -> str:
        return hmac.new(_SALT.encode(), p.encode(), hashlib.sha256).hexdigest()
    def verify_pass(p: str, h: str) -> bool:
        return hmac.compare_digest(hash_pass(p), h)

app = FastAPI(title="MyKaBo")

# ─── CORS ─────────────────────────────────────────────────────────────────────
_ALLOWED_ORIGINS = [o.strip() for o in
    os.environ.get("ALLOWED_ORIGINS", "http://localhost:8000").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB = os.environ.get("DB_PATH", "kanban.db")

# ─── AUTH CONFIG ──────────────────────────────────────────────────────────────
USERNAME    = os.environ.get("KANBAN_USER", "admin")
_RAW_PASS   = os.environ.get("KANBAN_PASS", "changeme")
PASS_HASH   = hash_pass(_RAW_PASS)
SESSION_TTL   = int(os.environ.get("SESSION_TTL_HOURS", "24"))
REMEMBER_TTL  = int(os.environ.get("REMEMBER_TTL_HOURS", str(30 * 24)))

# Brute-force protection: track failed attempts per IP
_failed: dict[str, list[float]] = {}   # ip -> list of epoch timestamps
_MAX_ATTEMPTS  = 10
_WINDOW_SECS   = 300   # 5 min window


def _is_locked(ip: str) -> bool:
    now = time.time()
    attempts = [t for t in _failed.get(ip, []) if now - t < _WINDOW_SECS]
    _failed[ip] = attempts
    return len(attempts) >= _MAX_ATTEMPTS

def _record_failure(ip: str):
    _failed.setdefault(ip, []).append(time.time())

def _clear_failures(ip: str):
    _failed.pop(ip, None)

def create_session(remember: bool = False) -> tuple[str, int]:
    """Returns (token, ttl_hours). Cleans up expired sessions as a side-effect."""
    ttl = REMEMBER_TTL if remember else SESSION_TTL
    tok = secrets.token_urlsafe(32)
    expires = time.time() + ttl * 3600
    conn = get_db()
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
    conn.execute("INSERT INTO sessions (token, expires_at) VALUES (?, ?)", (tok, expires))
    conn.commit()
    conn.close()
    return tok, ttl

def require_auth(session: Optional[str] = Cookie(default=None)) -> str:
    if not session:
        raise HTTPException(401, "Non authentifié")
    conn = get_db()
    row = conn.execute(
        "SELECT expires_at FROM sessions WHERE token = ?", (session,)
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(401, "Non authentifié")
    if time.time() > row["expires_at"]:
        conn = get_db()
        conn.execute("DELETE FROM sessions WHERE token = ?", (session,))
        conn.commit()
        conn.close()
        raise HTTPException(401, "Session expirée")
    return session

# Explicit allowlists to prevent SQL-injection via field names
_TASK_WRITABLE = frozenset({
    "title","description","column","color","category",
    "priority","due_date","position","stack_id","stack_pos","recurrence",
    "snooze_until",
})
_STACK_MOVE_WRITABLE = frozenset({"column","position"})

# ─── DB ───────────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")   # safer concurrent reads
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS tasks (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        title       TEXT NOT NULL,
        description TEXT DEFAULT '',
        column      TEXT NOT NULL DEFAULT 'backlog',
        color       TEXT DEFAULT '#fef08a',
        category    TEXT DEFAULT '',
        priority    TEXT DEFAULT 'normal',
        due_date    TEXT DEFAULT NULL,
        position    INTEGER DEFAULT 0,
        archived    INTEGER DEFAULT 0,
        archived_at TEXT DEFAULT NULL,
        stack_id    TEXT DEFAULT NULL,
        stack_pos   INTEGER DEFAULT 0,
        created_at  TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS comments (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id    INTEGER NOT NULL,
        content    TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS checklist_items (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id  INTEGER NOT NULL,
        text     TEXT NOT NULL,
        checked  INTEGER DEFAULT 0,
        position INTEGER DEFAULT 0,
        FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS task_dependencies (
        task_id       INTEGER NOT NULL,
        depends_on_id INTEGER NOT NULL,
        PRIMARY KEY (task_id, depends_on_id),
        FOREIGN KEY (task_id)       REFERENCES tasks(id) ON DELETE CASCADE,
        FOREIGN KEY (depends_on_id) REFERENCES tasks(id) ON DELETE CASCADE
    )""")
    # Unique constraint on (stack_id, stack_pos) — apply only if not exists
    c.execute("""CREATE UNIQUE INDEX IF NOT EXISTS
        uq_stack_pos ON tasks(stack_id, stack_pos)
        WHERE stack_id IS NOT NULL""")
    # Migrations
    existing = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    for col, defn in [
        ("archived",     "INTEGER DEFAULT 0"),
        ("archived_at",  "TEXT DEFAULT NULL"),
        ("stack_id",     "TEXT DEFAULT NULL"),
        ("stack_pos",    "INTEGER DEFAULT 0"),
        ("recurrence",   "TEXT DEFAULT NULL"),
        ("snooze_until", "TEXT DEFAULT NULL"),
    ]:
        if col not in existing:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {defn}")
    c.execute("""CREATE TABLE IF NOT EXISTS sessions (
        token      TEXT PRIMARY KEY,
        expires_at REAL NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS wip_limits (
        col_id    TEXT PRIMARY KEY,
        max_tasks INTEGER NOT NULL CHECK(max_tasks > 0)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS categories (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        name     TEXT NOT NULL UNIQUE,
        position INTEGER NOT NULL DEFAULT 0
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS config (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""")
    # One-time seed: import distinct categories from existing tasks
    if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
        distinct = conn.execute(
            "SELECT category, COUNT(*) AS cnt FROM tasks "
            "WHERE category IS NOT NULL AND category != '' "
            "GROUP BY category ORDER BY cnt DESC, category"
        ).fetchall()
        for i, row in enumerate(distinct):
            conn.execute(
                "INSERT OR IGNORE INTO categories (name, position) VALUES (?, ?)",
                (row["category"], i)
            )
    conn.commit()
    conn.close()

init_db()

def _load_stored_credentials():
    global USERNAME, PASS_HASH
    conn = get_db()
    rows = conn.execute(
        "SELECT key, value FROM config WHERE key IN ('username','pass_hash')"
    ).fetchall()
    conn.close()
    for row in rows:
        if row["key"] == "username":
            USERNAME = row["value"]
        elif row["key"] == "pass_hash":
            PASS_HASH = row["value"]

_load_stored_credentials()

# ─── MODELS ───────────────────────────────────────────────────────────────────
COLUMNS = ["backlog","todo","inprogress","blocked","done","abandoned"]

class LoginRequest(BaseModel):
    username: str
    password: str
    remember: bool = False

class TaskCreate(BaseModel):
    title:        str
    description:  Optional[str] = ""
    column:       Optional[str] = "backlog"
    color:        Optional[str] = "#fef08a"
    category:     Optional[str] = ""
    priority:     Optional[str] = "normal"
    due_date:     Optional[str] = None
    recurrence:   Optional[str] = None
    snooze_until: Optional[str] = None

class TaskUpdate(BaseModel):
    title:        Optional[str] = None
    description:  Optional[str] = None
    column:       Optional[str] = None
    color:        Optional[str] = None
    category:     Optional[str] = None
    priority:     Optional[str] = None
    due_date:     Optional[str] = None
    position:     Optional[int] = None
    stack_id:     Optional[str] = None
    stack_pos:    Optional[int] = None
    recurrence:   Optional[str] = None
    snooze_until: Optional[str] = None

class CommentCreate(BaseModel):
    content: str

class ChecklistItemCreate(BaseModel):
    text: str

class ChecklistItemUpdate(BaseModel):
    text:     Optional[str] = None
    checked:  Optional[int] = None
    position: Optional[int] = None

class DependencyUpdate(BaseModel):
    depends_on: list[int] = []

class StackCreate(BaseModel):
    task_ids: list[int]

class WipLimitUpdate(BaseModel):
    max_tasks: Optional[int] = None   # None or ≤0  removes the limit

class ChangeCredentials(BaseModel):
    current_password: str
    new_username: Optional[str] = None
    new_password: Optional[str] = None

class CategoryCreate(BaseModel):
    name: str

class CategoryUpdate(BaseModel):
    name:     Optional[str] = None
    position: Optional[int] = None

class ImportRequest(BaseModel):
    mode: str   # "merge" | "replace"
    data: Any

RECURRENCES = {"daily", "weekly", "monthly", "yearly"}

def _next_due(due_str: str | None, recurrence: str) -> str | None:
    if not due_str:
        return None
    d = date.fromisoformat(due_str)
    if recurrence == "daily":
        return (d + timedelta(days=1)).isoformat()
    if recurrence == "weekly":
        return (d + timedelta(weeks=1)).isoformat()
    if recurrence == "monthly":
        month = d.month % 12 + 1
        year  = d.year + (1 if d.month == 12 else 0)
        day   = min(d.day, calendar.monthrange(year, month)[1])
        return date(year, month, day).isoformat()
    if recurrence == "yearly":
        year = d.year + 1
        day  = min(d.day, calendar.monthrange(year, d.month)[1])
        return date(year, d.month, day).isoformat()
    return due_str

def row_to_dict(r): return dict(r)

# Detect a cycle in a directed graph expressed as {node: set(neighbours)}.
# Edge a -> b means "task a is blocked by task b" (a depends on b).
def _has_cycle(edges: dict[int, set[int]]) -> bool:
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[int, int] = {}
    def visit(u: int) -> bool:
        color[u] = GRAY
        for v in edges.get(u, ()):
            cv = color.get(v, WHITE)
            if cv == GRAY:
                return True
            if cv == WHITE and visit(v):
                return True
        color[u] = BLACK
        return False
    for node in list(edges.keys()):
        if color.get(node, WHITE) == WHITE and visit(node):
            return True
    return False

# Returns the canonical category value. Empty/None clears it; otherwise it must
# match an existing category name in the categories table (strict mode).
def _validate_category(conn, value) -> str:
    if value is None or value == "":
        return ""
    name = value.strip()
    if not name:
        return ""
    if not conn.execute("SELECT 1 FROM categories WHERE name=?", (name,)).fetchone():
        raise HTTPException(
            400,
            f"Cat\u00e9gorie inconnue : \u00ab\u00a0{name}\u00a0\u00bb. Cr\u00e9ez-la d'abord via la gestion des cat\u00e9gories."
        )
    return name

# ─── AUTH ─────────────────────────────────────────────────────────────────────
@app.post("/api/login")
def login(req: LoginRequest, request: Request, response: Response):
    ip = request.client.host if request.client else "unknown"
    if _is_locked(ip):
        raise HTTPException(429, "Trop de tentatives. Réessayez dans quelques minutes.")
    if req.username != USERNAME or not verify_pass(req.password, PASS_HASH):
        _record_failure(ip)
        raise HTTPException(401, "Identifiants incorrects")
    _clear_failures(ip)
    tok, ttl = create_session(req.remember)
    response.set_cookie("session", tok, httponly=True, samesite="lax",
                        max_age=ttl * 3600)
    return {"ok": True}

@app.post("/api/logout")
def logout(response: Response, session: Optional[str] = Cookie(default=None)):
    if session:
        conn = get_db()
        conn.execute("DELETE FROM sessions WHERE token = ?", (session,))
        conn.commit()
        conn.close()
    response.delete_cookie("session")
    return {"ok": True}

@app.get("/api/me")
def me(session: str = Depends(require_auth)):
    return {"username": USERNAME}

@app.patch("/api/credentials")
def change_credentials(body: ChangeCredentials, session: str = Depends(require_auth)):
    global USERNAME, PASS_HASH
    if not verify_pass(body.current_password, PASS_HASH):
        raise HTTPException(401, "Mot de passe actuel incorrect")
    new_username = body.new_username.strip() if body.new_username else None
    new_password = body.new_password
    if not new_username and not new_password:
        raise HTTPException(400, "Aucune modification demandée")
    if new_username and len(new_username) < 3:
        raise HTTPException(400, "Le nom d'utilisateur doit comporter au moins 3 caractères")
    if new_password and len(new_password) < 6:
        raise HTTPException(400, "Le mot de passe doit comporter au moins 6 caractères")
    conn = get_db()
    if new_username:
        USERNAME = new_username
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES ('username', ?)", (new_username,)
        )
    if new_password:
        PASS_HASH = hash_pass(new_password)
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES ('pass_hash', ?)", (PASS_HASH,)
        )
    conn.commit()
    conn.close()
    return {"ok": True, "username": USERNAME}

# ─── TASKS ────────────────────────────────────────────────────────────────────
@app.get("/api/tasks")
def get_tasks(session: str = Depends(require_auth)):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE archived=0 ORDER BY column, position, id"
    ).fetchall()
    # Inline latest block reason to avoid N+1 on the frontend
    block_reasons: dict[int, str] = {}
    blocked_ids = [r["id"] for r in rows if r["column"] == "blocked"]
    for tid in blocked_ids:
        last = conn.execute(
            """SELECT content FROM comments WHERE task_id=?
               AND content LIKE '🚧 [BLOQUÉ%'
               ORDER BY created_at DESC, id DESC LIMIT 1""", (tid,)
        ).fetchone()
        if last:
            m = re.search(r"Cause : (.+)$", last["content"])
            block_reasons[tid] = m.group(1) if m else last["content"]
    # Batch checklist progress counts
    all_ids = [r["id"] for r in rows]
    checklist_counts: dict[int, dict] = {}
    if all_ids:
        ph = ",".join("?" * len(all_ids))
        for r in conn.execute(
            f"SELECT task_id, COUNT(*) AS total, SUM(checked) AS done "
            f"FROM checklist_items WHERE task_id IN ({ph}) GROUP BY task_id",
            all_ids,
        ).fetchall():
            checklist_counts[r["task_id"]] = {"total": r["total"], "done": r["done"] or 0}
    # Batch dependency info ("blocked by" relationships)
    deps_map: dict[int, list[int]] = {}
    for r in conn.execute(
        "SELECT task_id, depends_on_id FROM task_dependencies"
    ).fetchall():
        deps_map.setdefault(r["task_id"], []).append(r["depends_on_id"])
    # A prerequisite is considered satisfied once it reaches a terminal column
    # ('done' = completed, 'abandoned' = discarded — either way it won't block).
    _RESOLVED_COLS = {"done", "abandoned"}
    status_map = {
        r["id"]: r["column"]
        for r in conn.execute("SELECT id, column FROM tasks").fetchall()
    }
    result = {col: [] for col in COLUMNS}
    for t in rows:
        d = row_to_dict(t)
        if d["column"] == "blocked" and d["id"] in block_reasons:
            d["block_reason"] = block_reasons[d["id"]]
        if d["id"] in checklist_counts:
            d["checklist_count"] = checklist_counts[d["id"]]
        if d["id"] in deps_map:
            dep_ids = deps_map[d["id"]]
            open_count = sum(
                1 for pid in dep_ids if status_map.get(pid) not in _RESOLVED_COLS
            )
            d["deps"] = {"total": len(dep_ids), "open": open_count}
        col = d["column"]
        if col in result:
            result[col].append(d)
    conn.close()
    return result

@app.get("/api/tasks/archived")
def get_archived(session: str = Depends(require_auth)):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE archived=1 ORDER BY archived_at DESC"
    ).fetchall()
    conn.close()
    return [row_to_dict(r) for r in rows]

@app.post("/api/tasks", status_code=201)
def create_task(task: TaskCreate, session: str = Depends(require_auth)):
    conn = get_db()
    category = _validate_category(conn, task.category)
    mp = conn.execute(
        "SELECT COALESCE(MAX(position),0) FROM tasks WHERE column=?", (task.column,)
    ).fetchone()[0]
    recurrence = task.recurrence if task.recurrence in RECURRENCES else None
    c = conn.cursor()
    c.execute(
        "INSERT INTO tasks (title,description,column,color,category,priority,due_date,position,recurrence,snooze_until)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (task.title, task.description, task.column, task.color,
         category, task.priority, task.due_date, mp + 1, recurrence,
         task.snooze_until or None)
    )
    tid = c.lastrowid
    conn.commit()
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    conn.close()
    return row_to_dict(row)

@app.get("/api/tasks/{task_id}")
def get_task(task_id: int, session: str = Depends(require_auth)):
    conn = get_db()
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Task not found")
    d = row_to_dict(row)
    d["comments"] = [row_to_dict(r) for r in conn.execute(
        "SELECT * FROM comments WHERE task_id=? ORDER BY created_at", (task_id,)
    ).fetchall()]
    d["checklist"] = [row_to_dict(r) for r in conn.execute(
        "SELECT * FROM checklist_items WHERE task_id=? ORDER BY position, id", (task_id,)
    ).fetchall()]
    d["depends_on"] = [row_to_dict(r) for r in conn.execute(
        "SELECT t.id, t.title, t.column FROM task_dependencies dp "
        "JOIN tasks t ON t.id = dp.depends_on_id "
        "WHERE dp.task_id=? ORDER BY t.title", (task_id,)
    ).fetchall()]
    conn.close()
    return d

@app.patch("/api/tasks/{task_id}")
def update_task(task_id: int, update: TaskUpdate, session: str = Depends(require_auth)):
    conn = get_db()
    if not conn.execute("SELECT id FROM tasks WHERE id=?", (task_id,)).fetchone():
        conn.close()
        raise HTTPException(404, "Task not found")
    fields = {k: v for k, v in update.model_dump().items()
              if v is not None and k in _TASK_WRITABLE}
    if "category" in fields:
        fields["category"] = _validate_category(conn, fields["category"])
    if "recurrence" in fields and fields["recurrence"] not in RECURRENCES:
        fields.pop("recurrence")
    if "snooze_until" in fields and not fields["snooze_until"]:
        fields["snooze_until"] = None
    if fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE tasks SET {sets} WHERE id=?", (*fields.values(), task_id))
        conn.commit()
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    conn.close()
    return row_to_dict(row)

@app.post("/api/tasks/{task_id}/archive")
def archive_task(task_id: int, session: str = Depends(require_auth)):
    conn = get_db()
    task_row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not task_row:
        conn.close()
        raise HTTPException(404, "Task not found")
    task_data = row_to_dict(task_row)
    sid = task_data.get("stack_id")
    conn.execute(
        "UPDATE tasks SET archived=1, archived_at=?, stack_id=NULL WHERE id=?",
        (datetime.now(timezone.utc).isoformat(), task_id)
    )
    conn.commit()
    if sid:
        remaining = conn.execute(
            "SELECT id FROM tasks WHERE stack_id=? AND archived=0", (sid,)
        ).fetchall()
        if len(remaining) < 2:
            for r in remaining:
                conn.execute("UPDATE tasks SET stack_id=NULL, stack_pos=0 WHERE id=?", (r["id"],))
            conn.commit()
    # Spawn next occurrence for recurring tasks
    next_task = None
    if task_data.get("recurrence") in RECURRENCES:
        next_due = _next_due(task_data.get("due_date"), task_data["recurrence"])
        mp = conn.execute(
            "SELECT COALESCE(MAX(position),0) FROM tasks WHERE column='backlog'"
        ).fetchone()[0]
        c = conn.cursor()
        c.execute(
            "INSERT INTO tasks (title,description,column,color,category,priority,"
            "due_date,position,recurrence) VALUES (?,?,?,?,?,?,?,?,?)",
            (task_data["title"], task_data["description"], "backlog",
             task_data["color"], task_data["category"], task_data["priority"],
             next_due, mp + 1, task_data["recurrence"])
        )
        new_id = c.lastrowid
        conn.commit()
        next_task = row_to_dict(conn.execute("SELECT * FROM tasks WHERE id=?", (new_id,)).fetchone())
    conn.close()
    return {"ok": True, "next_task": next_task}

@app.post("/api/tasks/{task_id}/unarchive")
def unarchive_task(task_id: int, session: str = Depends(require_auth)):
    conn = get_db()
    if not conn.execute("SELECT id FROM tasks WHERE id=?", (task_id,)).fetchone():
        conn.close()
        raise HTTPException(404, "Task not found")
    conn.execute(
        "UPDATE tasks SET archived=0, archived_at=NULL WHERE id=?", (task_id,)
    )
    conn.commit()
    conn.close()
    return {"ok": True}

@app.delete("/api/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, session: str = Depends(require_auth)):
    conn = get_db()
    # Dissolve stack if needed before deleting
    row = conn.execute("SELECT stack_id FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row and row["stack_id"]:
        sid = row["stack_id"]
        conn.execute("UPDATE tasks SET stack_id=NULL, stack_pos=0 WHERE id=?", (task_id,))
        remaining = conn.execute(
            "SELECT id FROM tasks WHERE stack_id=?", (sid,)
        ).fetchall()
        if len(remaining) < 2:
            for r in remaining:
                conn.execute("UPDATE tasks SET stack_id=NULL, stack_pos=0 WHERE id=?", (r["id"],))
    conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    conn.commit()
    conn.close()

# ─── WIP LIMITS ───────────────────────────────────────────────────────────────
@app.get("/api/wip_limits")
def get_wip_limits(session: str = Depends(require_auth)):
    conn = get_db()
    rows = conn.execute("SELECT col_id, max_tasks FROM wip_limits").fetchall()
    conn.close()
    return {r["col_id"]: r["max_tasks"] for r in rows}

@app.patch("/api/wip_limits/{col_id}")
def set_wip_limit(col_id: str, body: WipLimitUpdate,
                  session: str = Depends(require_auth)):
    if col_id not in COLUMNS:
        raise HTTPException(400, f"Colonne inconnue : {col_id}")
    conn = get_db()
    if body.max_tasks is None or body.max_tasks <= 0:
        conn.execute("DELETE FROM wip_limits WHERE col_id=?", (col_id,))
    else:
        conn.execute(
            "INSERT OR REPLACE INTO wip_limits (col_id, max_tasks) VALUES (?, ?)",
            (col_id, body.max_tasks),
        )
    conn.commit()
    rows = conn.execute("SELECT col_id, max_tasks FROM wip_limits").fetchall()
    conn.close()
    return {r["col_id"]: r["max_tasks"] for r in rows}

# ─── CATEGORIES ───────────────────────────────────────────────────────────────
@app.get("/api/categories")
def list_categories(session: str = Depends(require_auth)):
    conn = get_db()
    rows = conn.execute("SELECT * FROM categories ORDER BY position, name").fetchall()
    conn.close()
    return [row_to_dict(r) for r in rows]

@app.post("/api/categories", status_code=201)
def create_category(body: CategoryCreate, session: str = Depends(require_auth)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Le nom est requis.")
    if len(name) > 50:
        raise HTTPException(400, "Nom trop long (50 caractères max).")
    conn = get_db()
    if conn.execute("SELECT 1 FROM categories WHERE name=?", (name,)).fetchone():
        conn.close()
        raise HTTPException(409, f"« {name} » existe déjà.")
    max_pos = conn.execute(
        "SELECT COALESCE(MAX(position), -1) FROM categories"
    ).fetchone()[0]
    c = conn.cursor()
    c.execute("INSERT INTO categories (name, position) VALUES (?, ?)",
              (name, max_pos + 1))
    cid = c.lastrowid
    conn.commit()
    row = conn.execute("SELECT * FROM categories WHERE id=?", (cid,)).fetchone()
    conn.close()
    return row_to_dict(row)

@app.patch("/api/categories/{cat_id}")
def update_category(cat_id: int, update: CategoryUpdate,
                    session: str = Depends(require_auth)):
    conn = get_db()
    row = conn.execute("SELECT * FROM categories WHERE id=?", (cat_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Catégorie introuvable")
    old_name = row["name"]
    if update.name is not None:
        new_name = update.name.strip()
        if not new_name:
            conn.close()
            raise HTTPException(400, "Le nom est requis.")
        if len(new_name) > 50:
            conn.close()
            raise HTTPException(400, "Nom trop long (50 caractères max).")
        if new_name != old_name:
            dup = conn.execute(
                "SELECT 1 FROM categories WHERE name=? AND id!=?",
                (new_name, cat_id)
            ).fetchone()
            if dup:
                conn.close()
                raise HTTPException(409, f"« {new_name} » existe déjà.")
            conn.execute("UPDATE categories SET name=? WHERE id=?", (new_name, cat_id))
            # Propagate rename to all tasks using this category
            conn.execute("UPDATE tasks SET category=? WHERE category=?",
                         (new_name, old_name))
    if update.position is not None:
        conn.execute("UPDATE categories SET position=? WHERE id=?",
                     (update.position, cat_id))
    conn.commit()
    row = conn.execute("SELECT * FROM categories WHERE id=?", (cat_id,)).fetchone()
    conn.close()
    return row_to_dict(row)

@app.delete("/api/categories/{cat_id}", status_code=204)
def delete_category(cat_id: int, session: str = Depends(require_auth)):
    conn = get_db()
    row = conn.execute("SELECT name FROM categories WHERE id=?", (cat_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Catégorie introuvable")
    # Silently clear the category on all affected tasks, then delete.
    conn.execute("UPDATE tasks SET category='' WHERE category=?", (row["name"],))
    conn.execute("DELETE FROM categories WHERE id=?", (cat_id,))
    conn.commit()
    conn.close()

# ─── COMMENTS ─────────────────────────────────────────────────────────────────
@app.post("/api/tasks/{task_id}/comments", status_code=201)
def add_comment(task_id: int, comment: CommentCreate,
                session: str = Depends(require_auth)):
    conn = get_db()
    if not conn.execute("SELECT id FROM tasks WHERE id=?", (task_id,)).fetchone():
        conn.close()
        raise HTTPException(404, "Task not found")
    c = conn.cursor()
    c.execute("INSERT INTO comments (task_id, content) VALUES (?,?)",
              (task_id, comment.content))
    cid = c.lastrowid
    conn.commit()
    row = conn.execute("SELECT * FROM comments WHERE id=?", (cid,)).fetchone()
    conn.close()
    return row_to_dict(row)

@app.delete("/api/tasks/{task_id}/comments/{comment_id}", status_code=204)
def delete_comment(task_id: int, comment_id: int,
                   session: str = Depends(require_auth)):
    conn = get_db()
    conn.execute("DELETE FROM comments WHERE id=? AND task_id=?",
                 (comment_id, task_id))
    conn.commit()
    conn.close()

# ─── CHECKLIST ────────────────────────────────────────────────────────────────
@app.post("/api/tasks/{task_id}/checklist", status_code=201)
def add_checklist_item(task_id: int, body: ChecklistItemCreate,
                       session: str = Depends(require_auth)):
    conn = get_db()
    if not conn.execute("SELECT id FROM tasks WHERE id=?", (task_id,)).fetchone():
        conn.close()
        raise HTTPException(404, "Task not found")
    max_pos = conn.execute(
        "SELECT COALESCE(MAX(position),0) FROM checklist_items WHERE task_id=?", (task_id,)
    ).fetchone()[0]
    c = conn.cursor()
    c.execute("INSERT INTO checklist_items (task_id, text, position) VALUES (?,?,?)",
              (task_id, body.text.strip(), max_pos + 1))
    cid = c.lastrowid
    conn.commit()
    row = conn.execute("SELECT * FROM checklist_items WHERE id=?", (cid,)).fetchone()
    conn.close()
    return row_to_dict(row)

@app.patch("/api/tasks/{task_id}/checklist/{item_id}")
def update_checklist_item(task_id: int, item_id: int, body: ChecklistItemUpdate,
                          session: str = Depends(require_auth)):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM checklist_items WHERE id=? AND task_id=?", (item_id, task_id)
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Checklist item not found")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "text" in fields:
        fields["text"] = fields["text"].strip() or fields.pop("text")
    was_checked = bool(row["checked"])
    now_checked = bool(fields.get("checked", was_checked))
    if fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE checklist_items SET {sets} WHERE id=?",
                     (*fields.values(), item_id))
    # Auto-comment when an item transitions unchecked → checked
    if not was_checked and now_checked:
        item_text = fields.get("text", row["text"])
        conn.execute(
            "INSERT INTO comments (task_id, content) VALUES (?,?)",
            (task_id, f"☑️ Sous-tâche réalisée : {item_text}")
        )
    conn.commit()
    row = conn.execute("SELECT * FROM checklist_items WHERE id=?", (item_id,)).fetchone()
    conn.close()
    return row_to_dict(row)

@app.delete("/api/tasks/{task_id}/checklist/{item_id}", status_code=204)
def delete_checklist_item(task_id: int, item_id: int,
                          session: str = Depends(require_auth)):
    conn = get_db()
    conn.execute("DELETE FROM checklist_items WHERE id=? AND task_id=?", (item_id, task_id))
    conn.commit()
    conn.close()

# ─── DEPENDENCIES ─────────────────────────────────────────────────────────────
@app.put("/api/tasks/{task_id}/dependencies")
def set_dependencies(task_id: int, body: DependencyUpdate,
                     session: str = Depends(require_auth)):
    conn = get_db()
    if not conn.execute("SELECT id FROM tasks WHERE id=?", (task_id,)).fetchone():
        conn.close()
        raise HTTPException(404, "Task not found")
    # Sanitise: drop self-references and duplicates, validate existence
    deps: list[int] = []
    seen: set[int] = set()
    for dep in body.depends_on:
        if dep == task_id or dep in seen:
            continue
        if not conn.execute(
            "SELECT id FROM tasks WHERE id=? AND archived=0", (dep,)
        ).fetchone():
            conn.close()
            raise HTTPException(400, f"Tâche {dep} introuvable")
        seen.add(dep)
        deps.append(dep)
    # Build the proposed graph (all edges except this task's, plus the new ones)
    edges: dict[int, set[int]] = {}
    for r in conn.execute(
        "SELECT task_id, depends_on_id FROM task_dependencies WHERE task_id!=?",
        (task_id,),
    ).fetchall():
        edges.setdefault(r["task_id"], set()).add(r["depends_on_id"])
    edges[task_id] = set(deps)
    if _has_cycle(edges):
        conn.close()
        raise HTTPException(400, "Dépendance circulaire détectée")
    conn.execute("DELETE FROM task_dependencies WHERE task_id=?", (task_id,))
    for dep in deps:
        conn.execute(
            "INSERT INTO task_dependencies (task_id, depends_on_id) VALUES (?,?)",
            (task_id, dep),
        )
    conn.commit()
    conn.close()
    return {"ok": True, "depends_on": deps}

# ─── STACKS ───────────────────────────────────────────────────────────────────
@app.post("/api/stacks", status_code=201)
def create_stack(body: StackCreate, session: str = Depends(require_auth)):
    if len(body.task_ids) < 2:
        raise HTTPException(400, "A stack needs at least 2 tasks")
    conn = get_db()
    for tid in body.task_ids:
        row = conn.execute(
            "SELECT id, archived FROM tasks WHERE id=?", (tid,)
        ).fetchone()
        if not row or row["archived"]:
            conn.close()
            raise HTTPException(404, f"Task {tid} not found or archived")
    # All tasks in a stack must share the same column (the representative's column).
    # This ensures that unstacking puts every task back in the right column,
    # even when the pile was created by dragging across columns.
    target_col = conn.execute(
        "SELECT column FROM tasks WHERE id=?", (body.task_ids[0],)
    ).fetchone()["column"]
    sid = secrets.token_urlsafe(8)
    for i, tid in enumerate(body.task_ids):
        conn.execute("UPDATE tasks SET stack_id=?, stack_pos=?, column=? WHERE id=?",
                     (sid, i, target_col, tid))
    conn.commit()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE stack_id=? ORDER BY stack_pos", (sid,)
    ).fetchall()
    conn.close()
    return {"stack_id": sid, "tasks": [row_to_dict(r) for r in rows]}

@app.get("/api/stacks/{stack_id}")
def get_stack(stack_id: str, session: str = Depends(require_auth)):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE stack_id=? ORDER BY stack_pos", (stack_id,)
    ).fetchall()
    if not rows:
        conn.close()
        raise HTTPException(404, "Stack not found")
    tasks = []
    for r in rows:
        d = row_to_dict(r)
        d["comments"] = [row_to_dict(c) for c in conn.execute(
            "SELECT * FROM comments WHERE task_id=? ORDER BY created_at", (d["id"],)
        ).fetchall()]
        tasks.append(d)
    conn.close()
    return {"stack_id": stack_id, "tasks": tasks}

@app.delete("/api/stacks/{stack_id}", status_code=200)
def unstack(stack_id: str, session: str = Depends(require_auth)):
    conn = get_db()
    conn.execute(
        "UPDATE tasks SET stack_id=NULL, stack_pos=0 WHERE stack_id=?", (stack_id,)
    )
    conn.commit()
    conn.close()
    return {"ok": True}

@app.delete("/api/stacks/{stack_id}/tasks/{task_id}", status_code=200)
def remove_from_stack(stack_id: str, task_id: int,
                      session: str = Depends(require_auth)):
    conn = get_db()
    conn.execute(
        "UPDATE tasks SET stack_id=NULL, stack_pos=0 WHERE id=? AND stack_id=?",
        (task_id, stack_id)
    )
    remaining = conn.execute(
        "SELECT id FROM tasks WHERE stack_id=?", (stack_id,)
    ).fetchall()
    if len(remaining) < 2:
        for r in remaining:
            conn.execute(
                "UPDATE tasks SET stack_id=NULL, stack_pos=0 WHERE id=?", (r["id"],)
            )
    else:
        for i, r in enumerate(remaining):
            conn.execute("UPDATE tasks SET stack_pos=? WHERE id=?", (i, r["id"]))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.patch("/api/stacks/{stack_id}/move")
def move_stack(stack_id: str, update: TaskUpdate,
               session: str = Depends(require_auth)):
    conn = get_db()
    fields = {k: v for k, v in update.model_dump().items()
              if v is not None and k in _STACK_MOVE_WRITABLE}
    if not fields:
        conn.close()
        raise HTTPException(400, "Nothing to update")

    new_col = fields.get("column")
    if new_col:
        # Handle block/unblock side-effects for stacks
        reps = conn.execute(
            "SELECT id, column FROM tasks WHERE stack_id=?", (stack_id,)
        ).fetchall()
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE tasks SET {sets} WHERE stack_id=?",
                     (*fields.values(), stack_id))
        conn.commit()
        # Return info needed by frontend for comments
        prev_cols = {r["id"]: r["column"] for r in reps}
        rows = conn.execute(
            "SELECT * FROM tasks WHERE stack_id=? ORDER BY stack_pos", (stack_id,)
        ).fetchall()
        conn.close()
        return {
            "tasks": [row_to_dict(r) for r in rows],
            "prev_cols": prev_cols,
            "new_col": new_col,
        }
    else:
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE tasks SET {sets} WHERE stack_id=?",
                     (*fields.values(), stack_id))
        conn.commit()
        rows = conn.execute(
            "SELECT * FROM tasks WHERE stack_id=? ORDER BY stack_pos", (stack_id,)
        ).fetchall()
        conn.close()
        return {"tasks": [row_to_dict(r) for r in rows]}

# ─── EXPORT / IMPORT ─────────────────────────────────────────────────────────
@app.get("/api/export")
def export_data(session: str = Depends(require_auth)):
    conn = get_db()
    rows      = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    cats      = conn.execute("SELECT * FROM categories ORDER BY position, name").fetchall()
    wip_rows  = conn.execute("SELECT col_id, max_tasks FROM wip_limits").fetchall()
    task_list = []
    for t in rows:
        d = row_to_dict(t)
        d["comments"] = [row_to_dict(c) for c in conn.execute(
            "SELECT content, created_at FROM comments WHERE task_id=? ORDER BY created_at",
            (d["id"],)
        ).fetchall()]
        d["depends_on"] = [r["depends_on_id"] for r in conn.execute(
            "SELECT depends_on_id FROM task_dependencies WHERE task_id=?", (d["id"],)
        ).fetchall()]
        task_list.append(d)
    conn.close()
    payload = {
        "schema_version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "categories": [row_to_dict(c) for c in cats],
        "wip_limits": {r["col_id"]: r["max_tasks"] for r in wip_rows},
        "tasks": task_list,
    }
    filename = f"mykabo-export-{date.today().isoformat()}.json"
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

@app.post("/api/import")
def import_data(body: ImportRequest, session: str = Depends(require_auth)):
    if body.mode not in ("merge", "replace"):
        raise HTTPException(400, "Mode invalide : utilisez 'merge' ou 'replace'")
    data = body.data
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise HTTPException(400, "Fichier invalide ou version de schéma non supportée")
    conn = get_db()
    try:
        if body.mode == "replace":
            conn.execute("DELETE FROM task_dependencies")
            conn.execute("DELETE FROM comments")
            conn.execute("DELETE FROM tasks")
            conn.execute("DELETE FROM categories")
            conn.execute("DELETE FROM wip_limits")
        # Categories
        for cat in data.get("categories", []):
            name = (cat.get("name") or "").strip()
            if not name:
                continue
            if not conn.execute("SELECT 1 FROM categories WHERE name=?", (name,)).fetchone():
                max_pos = conn.execute(
                    "SELECT COALESCE(MAX(position),-1) FROM categories"
                ).fetchone()[0]
                conn.execute("INSERT INTO categories (name, position) VALUES (?,?)",
                             (name, max_pos + 1))
        # WIP limits
        for col_id, max_tasks in data.get("wip_limits", {}).items():
            if col_id in COLUMNS and isinstance(max_tasks, int) and max_tasks > 0:
                conn.execute(
                    "INSERT OR REPLACE INTO wip_limits (col_id, max_tasks) VALUES (?,?)",
                    (col_id, max_tasks),
                )
        # Tasks — remap IDs and stack_ids to avoid collisions
        stack_id_map: dict[str, str] = {}
        id_map: dict[int, int] = {}   # old task id -> new task id
        imported = 0
        for t in data.get("tasks", []):
            old_sid = t.get("stack_id")
            new_sid = None
            if old_sid:
                if old_sid not in stack_id_map:
                    stack_id_map[old_sid] = secrets.token_urlsafe(8)
                new_sid = stack_id_map[old_sid]
            category = t.get("category") or ""
            if category and not conn.execute(
                "SELECT 1 FROM categories WHERE name=?", (category,)
            ).fetchone():
                category = ""
            recurrence = t.get("recurrence")
            if recurrence not in RECURRENCES:
                recurrence = None
            c = conn.cursor()
            c.execute(
                "INSERT INTO tasks (title,description,column,color,category,priority,"
                "due_date,position,archived,archived_at,stack_id,stack_pos,"
                "recurrence,snooze_until,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    t.get("title",""), t.get("description",""),
                    t.get("column","backlog"), t.get("color","#fef08a"),
                    category, t.get("priority","normal"),
                    t.get("due_date"), t.get("position",0),
                    int(bool(t.get("archived",0))), t.get("archived_at"),
                    new_sid, t.get("stack_pos",0),
                    recurrence, t.get("snooze_until"),
                    t.get("created_at", datetime.now(timezone.utc).isoformat()),
                ),
            )
            new_id = c.lastrowid
            old_id = t.get("id")
            if old_id is not None:
                id_map[old_id] = new_id
            for comment in t.get("comments", []):
                conn.execute(
                    "INSERT INTO comments (task_id, content, created_at) VALUES (?,?,?)",
                    (new_id, comment.get("content",""),
                     comment.get("created_at", datetime.now(timezone.utc).isoformat())),
                )
            imported += 1
        # Dependencies — remap old ids; skip any that didn't survive the import
        for t in data.get("tasks", []):
            src = id_map.get(t.get("id"))
            if src is None:
                continue
            for old_dep in t.get("depends_on", []) or []:
                tgt = id_map.get(old_dep)
                if tgt is None or tgt == src:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO task_dependencies (task_id, depends_on_id) "
                    "VALUES (?,?)", (src, tgt),
                )
        conn.commit()
        conn.close()
        return {"ok": True, "tasks_imported": imported}
    except HTTPException:
        conn.close()
        raise
    except Exception as e:
        conn.close()
        raise HTTPException(500, f"Erreur lors de l'import : {e}")

# ─── FRONTEND ─────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

# ─── CLI ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse, sys

    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="MyKaBo — outils d'administration en ligne de commande",
    )
    sub = parser.add_subparsers(dest="command")

    rc = sub.add_parser(
        "reset-credentials",
        help="Réinitialiser le nom d'utilisateur et/ou le mot de passe",
    )
    rc.add_argument("-u", "--username", metavar="LOGIN",
                    help="Nouveau nom d'utilisateur (min. 3 caractères)")
    rc.add_argument("-p", "--password", metavar="PASS",
                    help="Nouveau mot de passe (min. 6 caractères)")

    args = parser.parse_args()

    if args.command == "reset-credentials":
        if not args.username and not args.password:
            parser.error("Spécifiez au moins --username ou --password.")

        conn = get_db()
        errors = []

        if args.username:
            u = args.username.strip()
            if len(u) < 3:
                errors.append("Le nom d'utilisateur doit comporter au moins 3 caractères.")
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO config (key, value) VALUES ('username', ?)", (u,)
                )
                print(f"✓ Nom d'utilisateur mis à jour : {u}")

        if args.password:
            if len(args.password) < 6:
                errors.append("Le mot de passe doit comporter au moins 6 caractères.")
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO config (key, value) VALUES ('pass_hash', ?)",
                    (hash_pass(args.password),),
                )
                print("✓ Mot de passe mis à jour.")

        if errors:
            for e in errors:
                print(f"Erreur : {e}", file=sys.stderr)
            conn.close()
            sys.exit(1)

        conn.commit()
        conn.close()
        print("Les modifications seront prises en compte au prochain démarrage du serveur.")
    else:
        parser.print_help()
        sys.exit(0)

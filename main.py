from fastapi import FastAPI, HTTPException, Depends, Response, Cookie, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import sqlite3, os, secrets, time
from datetime import datetime, timedelta

try:
    from passlib.context import CryptContext
    pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    def hash_pass(p: str) -> str: return pwd_ctx.hash(p)
    def verify_pass(p: str, h: str) -> bool: return pwd_ctx.verify(p, h)
except ImportError:
    # Fallback if passlib not installed — still salted via hmac
    import hashlib, hmac
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
_LOCKOUT_SECS  = 600   # 10 min lockout

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
    "priority","due_date","position","stack_id","stack_pos",
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
    # Unique constraint on (stack_id, stack_pos) — apply only if not exists
    c.execute("""CREATE UNIQUE INDEX IF NOT EXISTS
        uq_stack_pos ON tasks(stack_id, stack_pos)
        WHERE stack_id IS NOT NULL""")
    # Migrations
    existing = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    for col, defn in [
        ("archived",    "INTEGER DEFAULT 0"),
        ("archived_at", "TEXT DEFAULT NULL"),
        ("stack_id",    "TEXT DEFAULT NULL"),
        ("stack_pos",   "INTEGER DEFAULT 0"),
    ]:
        if col not in existing:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {defn}")
    c.execute("""CREATE TABLE IF NOT EXISTS sessions (
        token      TEXT PRIMARY KEY,
        expires_at REAL NOT NULL
    )""")
    conn.commit()
    conn.close()

init_db()

# ─── MODELS ───────────────────────────────────────────────────────────────────
COLUMNS = ["backlog","todo","inprogress","blocked","done","abandoned"]

class LoginRequest(BaseModel):
    username: str
    password: str
    remember: bool = False

class TaskCreate(BaseModel):
    title:       str
    description: Optional[str] = ""
    column:      Optional[str] = "backlog"
    color:       Optional[str] = "#fef08a"
    category:    Optional[str] = ""
    priority:    Optional[str] = "normal"
    due_date:    Optional[str] = None

class TaskUpdate(BaseModel):
    title:       Optional[str] = None
    description: Optional[str] = None
    column:      Optional[str] = None
    color:       Optional[str] = None
    category:    Optional[str] = None
    priority:    Optional[str] = None
    due_date:    Optional[str] = None
    position:    Optional[int] = None
    stack_id:    Optional[str] = None
    stack_pos:   Optional[int] = None

class CommentCreate(BaseModel):
    content: str

class StackCreate(BaseModel):
    task_ids: list[int]

def row_to_dict(r): return dict(r)

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
               ORDER BY created_at DESC LIMIT 1""", (tid,)
        ).fetchone()
        if last:
            import re
            m = re.search(r"Cause : (.+)$", last["content"])
            block_reasons[tid] = m.group(1) if m else last["content"]
    result = {col: [] for col in COLUMNS}
    for t in rows:
        d = row_to_dict(t)
        if d["column"] == "blocked" and d["id"] in block_reasons:
            d["block_reason"] = block_reasons[d["id"]]
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
    mp = conn.execute(
        "SELECT COALESCE(MAX(position),0) FROM tasks WHERE column=?", (task.column,)
    ).fetchone()[0]
    c = conn.cursor()
    c.execute(
        "INSERT INTO tasks (title,description,column,color,category,priority,due_date,position)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (task.title, task.description, task.column, task.color,
         task.category, task.priority, task.due_date, mp + 1)
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
        conn.close(); raise HTTPException(404, "Task not found")
    d = row_to_dict(row)
    d["comments"] = [row_to_dict(r) for r in conn.execute(
        "SELECT * FROM comments WHERE task_id=? ORDER BY created_at", (task_id,)
    ).fetchall()]
    conn.close()
    return d

@app.patch("/api/tasks/{task_id}")
def update_task(task_id: int, update: TaskUpdate, session: str = Depends(require_auth)):
    conn = get_db()
    if not conn.execute("SELECT id FROM tasks WHERE id=?", (task_id,)).fetchone():
        conn.close(); raise HTTPException(404, "Task not found")
    fields = {k: v for k, v in update.dict().items()
              if v is not None and k in _TASK_WRITABLE}
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
    if not conn.execute("SELECT id FROM tasks WHERE id=?", (task_id,)).fetchone():
        conn.close(); raise HTTPException(404, "Task not found")
    conn.execute(
        "UPDATE tasks SET archived=1, archived_at=?, stack_id=NULL WHERE id=?",
        (datetime.utcnow().isoformat(), task_id)
    )
    conn.commit()
    # Dissolve stack if only 1 member remains
    row = conn.execute("SELECT stack_id FROM tasks WHERE id=?", (task_id,)).fetchone()
    sid = row["stack_id"] if row else None
    if sid:
        remaining = conn.execute(
            "SELECT id FROM tasks WHERE stack_id=? AND archived=0", (sid,)
        ).fetchall()
        if len(remaining) < 2:
            for r in remaining:
                conn.execute("UPDATE tasks SET stack_id=NULL, stack_pos=0 WHERE id=?", (r["id"],))
            conn.commit()
    conn.close()
    return {"ok": True}

@app.post("/api/tasks/{task_id}/unarchive")
def unarchive_task(task_id: int, session: str = Depends(require_auth)):
    conn = get_db()
    if not conn.execute("SELECT id FROM tasks WHERE id=?", (task_id,)).fetchone():
        conn.close(); raise HTTPException(404, "Task not found")
    conn.execute(
        "UPDATE tasks SET archived=0, archived_at=NULL WHERE id=?", (task_id,)
    )
    conn.commit(); conn.close()
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
    conn.commit(); conn.close()

# ─── COMMENTS ─────────────────────────────────────────────────────────────────
@app.post("/api/tasks/{task_id}/comments", status_code=201)
def add_comment(task_id: int, comment: CommentCreate,
                session: str = Depends(require_auth)):
    conn = get_db()
    if not conn.execute("SELECT id FROM tasks WHERE id=?", (task_id,)).fetchone():
        conn.close(); raise HTTPException(404, "Task not found")
    c = conn.cursor()
    c.execute("INSERT INTO comments (task_id, content) VALUES (?,?)",
              (task_id, comment.content))
    cid = c.lastrowid; conn.commit()
    row = conn.execute("SELECT * FROM comments WHERE id=?", (cid,)).fetchone()
    conn.close()
    return row_to_dict(row)

@app.delete("/api/tasks/{task_id}/comments/{comment_id}", status_code=204)
def delete_comment(task_id: int, comment_id: int,
                   session: str = Depends(require_auth)):
    conn = get_db()
    conn.execute("DELETE FROM comments WHERE id=? AND task_id=?",
                 (comment_id, task_id))
    conn.commit(); conn.close()

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
            conn.close(); raise HTTPException(404, f"Task {tid} not found or archived")
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
        conn.close(); raise HTTPException(404, "Stack not found")
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
    conn.commit(); conn.close()
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
    conn.commit(); conn.close()
    return {"ok": True}

@app.patch("/api/stacks/{stack_id}/move")
def move_stack(stack_id: str, update: TaskUpdate,
               session: str = Depends(require_auth)):
    conn = get_db()
    fields = {k: v for k, v in update.dict().items()
              if v is not None and k in _STACK_MOVE_WRITABLE}
    if not fields:
        conn.close(); raise HTTPException(400, "Nothing to update")

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

# ─── FRONTEND ─────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

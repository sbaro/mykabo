# MyKaBo — Claude Code Project Context

> **MyKaBo** (My Kanban Board) is a self-hosted, single-user Kanban board
> running in Docker. Backend: Python/FastAPI + SQLite. Frontend: vanilla
> HTML/CSS/JS served by FastAPI. Accessible from any browser (Mac, PC, iPhone).

---

## Quick start

```bash
docker compose up -d --build
# → http://localhost:8000
```

Default credentials (override via env vars):
- User: `admin`
- Pass: `changeme`

---

## Repository layout

```
mykabot/
├── main.py               # FastAPI backend (single file)
├── index.html            # Frontend (vanilla JS, served by FastAPI)
├── requirements.txt      # Python deps
├── Dockerfile
├── docker-compose.yml
├── CLAUDE.md             # ← this file
└── docs/
    ├── architecture.md   # System design & data model
    ├── api.md            # All REST endpoints
    ├── frontend.md       # Frontend architecture & conventions
    └── known-issues.md   # Remaining TODOs & deferred items
```

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `KANBAN_USER` | `admin` | Login username |
| `KANBAN_PASS` | `changeme` | Login password (bcrypt-hashed at startup) |
| `DB_PATH` | `kanban.db` | Path to SQLite file |
| `SESSION_TTL_HOURS` | `24` | Session lifetime in hours (sans "Se souvenir de moi") |
| `REMEMBER_TTL_HOURS` | `720` | Session lifetime en heures avec "Se souvenir de moi" (défaut 30 jours) |
| `ALLOWED_ORIGINS` | `http://localhost:8000` | Comma-separated CORS origins |

---

## Tech stack

| Layer | Choice | Notes |
|-------|--------|-------|
| Backend | FastAPI (Python 3.12) | Single `main.py` |
| Database | SQLite via `sqlite3` stdlib | WAL mode, FK enforcement |
| Password hashing | `passlib[bcrypt]` | HMAC-SHA256 fallback if not installed |
| Frontend | Vanilla HTML/CSS/JS | No build step, no framework |
| Container | Docker + Compose | SQLite persisted via named volume |

---

## Key conventions

- **Single-file backend**: all routes in `main.py`. Do not split into routers unless the file exceeds ~600 lines.
- **Single-file frontend**: all HTML/CSS/JS in `index.html`. No bundler.
- **No localStorage/sessionStorage**: all state is in JS variables or fetched from the API.
- **Auth**: cookie-based session (`httponly`, `samesite=lax`). Sessions stockées dans la table SQLite `sessions` (persistent across restarts). TTL 24 h par défaut, 30 jours si "Se souvenir de moi". IP-based brute-force protection (10 attempts / 5 min window).
- **SQL safety**: all dynamic field names validated against explicit frozenset allowlists (`_TASK_WRITABLE`, `_STACK_MOVE_WRITABLE`). Parameterised queries everywhere.
- **Error handling**: every API call in the frontend goes through `apiFetch()` which surfaces errors via a toast notification. No silent `catch {}`.
- **N+1 avoidance**: block reasons are resolved inline in `GET /api/tasks` (single SQL query per blocked task), not fetched per-card in the frontend.

---

## What to do next (suggested)

See `docs/known-issues.md` for the full deferred list. Top priorities:

1. Add `passlib` to `requirements.txt` and pin all transitive deps with `pip freeze`.
2. Add `secure=True` to the session cookie once HTTPS/reverse-proxy is configured.
3. ~~Replace in-memory session dict with a persistent store~~ ✅ Done — sessions table in SQLite.
4. Add a `GET /api/health` endpoint for the Docker healthcheck.
5. Consider pagination for the archived tasks endpoint.

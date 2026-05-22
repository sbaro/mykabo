# Known Issues & Deferred Work

Issues are grouped by severity. Items marked ✅ were fixed during the initial
development session. Remaining items are open.

---

## 🔴 High priority

### ~~1. Sessions lost on restart~~ ✅ Fixed
Sessions are now stored in the `sessions` SQLite table and survive container restarts.
A "Se souvenir de moi" checkbox on the login form extends the TTL to 30 days.

### 2. `secure=True` missing from session cookie
The cookie is set without `secure=True`, which means it can be transmitted over
plain HTTP. This is intentional for local dev but **must** be enabled in production.

**Fix**: set `secure=True` in `response.set_cookie(...)` once the app is behind
an HTTPS reverse proxy (nginx, Caddy, Traefik, etc.).

### ~~3. `passlib` may not be installed~~ ✅ Fixed
`passlib[bcrypt]==1.7.4` and `bcrypt==4.1.3` added to `requirements.txt`.

---

## 🟠 Medium priority

### 4. No `GET /api/health` endpoint
The Docker healthcheck in `docker-compose.yml` currently hits `/api/me`, which
requires auth and returns 401 for unauthenticated requests. Docker treats any
non-5xx as healthy, so it works — but a dedicated `/api/health` returning 200
unconditionally would be cleaner.

### 5. No unique constraint enforced on `(stack_id, stack_pos)` at the application level
The SQLite partial unique index (`uq_stack_pos`) enforces uniqueness at the DB
level, but the application code does not catch `IntegrityError` from SQLite when
concurrent writes race. Add a try/except around stack creation/reorder.

### 6. `archiveAll` archives stacked tasks without re-checking stack integrity
If a column contains a partial stack (some members in other columns), archiving
"all" in that column will archive the representative but leave stacked members
in other columns pointing to a dissolved stack. The backend `archive_task`
endpoint handles this correctly per-task, but a column-level atomic check would
be safer.

### 7. No pagination on `/api/tasks/archived`
As the archive grows, this endpoint returns all records in one query. Add
`limit`/`offset` query parameters.

### 8. Block reason resolution is O(N blocked tasks) on every board load
`GET /api/tasks` runs one SQL query per blocked task to find the latest `🚧`
comment. For a small board this is fine, but with many blocked tasks it adds up.

**Fix options**:
- Denormalise: add a `block_reason` column to `tasks`, updated when a block comment is posted.
- Or: use a single SQL query with `GROUP BY` / window function.

---

## 🟡 Low priority / Quality of life

### 9. Transitive dependencies not pinned
Only 3 packages are explicitly listed in `requirements.txt`. Run
`pip freeze > requirements.txt` after a clean install to pin everything.

### 10. No input length validation on task fields
`title`, `description`, `category`, and `comment.content` have no max-length
enforcement at the API level. A very long title will render awkwardly in the UI.

**Fix**: add Pydantic `Field(max_length=...)` constraints.

### 11. `move_stack` position parameter is ignored
`PATCH /api/stacks/{id}/move` accepts `position` but always assigns `position=0`
to all moved tasks. Relative ordering within the target column is not preserved
when moving an entire stack via drag & drop.

### 12. Stack representative changes are not surfaced to the user
When a task is ejected from a stack, the next task (new `stack_pos=0`) becomes
the representative silently. There is no visual confirmation or choice offered
to the user.

### 13. No offline / PWA support
The app requires a live connection to the server. A service worker with cache-first
strategy for the static assets would allow the UI to load offline.

### 14. Mobile drag & drop is unsupported
The HTML5 Drag and Drop API does not fire on touch devices. Cards can be reordered
and moved only via the edit modal on iPhone/iPad. Consider adding a touch-based
drag library (e.g. SortableJS) for mobile support.

### 15. No multi-language support
All UI strings are hardcoded in French. If internationalisation is ever needed,
strings should be extracted to a `i18n` object.

---

## ✅ Fixed during initial development session

- ~~Silent `catch {}` in all API calls~~ → replaced with `apiFetch` + toast
- ~~Duplicate `loadBoard` declaration~~ → removed
- ~~N+1 requests for block reasons~~ → resolved inline in `GET /api/tasks`
- ~~Drag state not reset on out-of-bounds drop~~ → `resetDragState()` in `dragend`
- ~~`stackHoverTimer` dead code~~ → removed
- ~~`esc(0)` returning `""`~~ → fixed null-check
- ~~SQL injection via dict keys~~ → `_TASK_WRITABLE` / `_STACK_MOVE_WRITABLE` allowlists
- ~~Unsalted SHA-256 password hashing~~ → `passlib[bcrypt]` with fallback
- ~~No CORS policy~~ → `CORSMiddleware` with `ALLOWED_ORIGINS` env var
- ~~Session memory leak~~ → `_purge_sessions()` on every login
- ~~No brute-force protection~~ → per-IP rate limiting with lockout
- ~~`SECRET_KEY` unused and misleading~~ → removed
- ~~No unique constraint on `(stack_id, stack_pos)`~~ → partial unique index added
- ~~Stack drag ignores block/unblock logic~~ → `move_stack` returns `prev_cols`/`new_col`
- ~~Archiving stack members leaves orphans~~ → `archive_task` dissolves stacks
- ~~Drop-to-first-position bug~~ → leading `drop-indicator` added per column
- ~~Ambiguous drag zones (reorder vs stack)~~ → three-zone system (top/middle/bottom thirds)

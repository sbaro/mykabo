# Architecture

## Overview

```
Browser  ──HTTP──▶  FastAPI (uvicorn)  ──▶  SQLite (WAL)
                        │
                    index.html (served as static HTML response)
```

FastAPI serves the frontend directly from `GET /`. There is no separate static
file server. All API routes are prefixed `/api/`.

---

## Database schema

### `tasks`

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK AUTOINCREMENT | |
| `title` | TEXT NOT NULL | |
| `description` | TEXT | Default `''` |
| `column` | TEXT | One of: `backlog`, `todo`, `inprogress`, `blocked`, `done`, `abandoned` |
| `color` | TEXT | Hex color string, e.g. `#fef08a` |
| `category` | TEXT | Free-form label |
| `priority` | TEXT | `low`, `normal`, `high` |
| `due_date` | TEXT | ISO date string `YYYY-MM-DD` or NULL |
| `position` | INTEGER | Sort order within column (0-based) |
| `archived` | INTEGER | 0 = active, 1 = archived |
| `archived_at` | TEXT | ISO datetime or NULL |
| `stack_id` | TEXT | Foreign key to logical stack (token string) or NULL |
| `stack_pos` | INTEGER | Position within stack (0 = representative shown on board) |
| `created_at` | TEXT | ISO datetime, default CURRENT_TIMESTAMP |

**Unique index**: `uq_stack_pos ON tasks(stack_id, stack_pos) WHERE stack_id IS NOT NULL`

### `comments`

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK AUTOINCREMENT | |
| `task_id` | INTEGER | FK → tasks(id) ON DELETE CASCADE |
| `content` | TEXT | Free text; system comments prefixed `🚧` or `✅` |
| `created_at` | TEXT | ISO datetime |

### `categories`

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK AUTOINCREMENT | |
| `name` | TEXT NOT NULL UNIQUE | The canonical label; tasks reference categories by this string in `tasks.category` |
| `position` | INTEGER NOT NULL DEFAULT 0 | Display order in the management drawer and dropdown |

**Curated list (strict mode)** — `POST/PATCH /api/tasks` reject any non-empty `category`
that does not match a row in this table. Renaming a category propagates to every task
that referenced the old name in the same transaction. Deleting a category clears the
field on affected tasks (silent: no reassignment prompt).

**Seed migration** — on first startup with this table empty, distinct non-empty
`category` values from existing tasks are imported, ordered by usage descending.

---

## Stack model

A **stack** is a logical grouping of tasks sharing a `stack_id` (random URL-safe token). There is no separate `stacks` table — the relationship is embedded in the `tasks` table.

- `stack_pos = 0` → the **representative** task, shown as the visible card on the board
- `stack_pos > 0` → hidden behind the representative; accessible via the stack drawer
- Moving a stack moves **all** tasks with that `stack_id` to the target column
- Archiving or deleting a task that is the last member of a stack dissolves the stack automatically
- A stack with fewer than 2 active members is automatically dissolved

---

## Session management

Sessions are stored in the `sessions` SQLite table (`token TEXT PRIMARY KEY, expires_at REAL`).
They survive container restarts. Expired sessions are purged on every login call.

On login, if `remember=true` is passed, the session TTL is `REMEMBER_TTL_HOURS` (default 720 h = 30 days)
instead of `SESSION_TTL_HOURS` (default 24 h). The cookie `max_age` is set to match.

**Brute-force protection**: `_failed: dict[str, list[float]]` tracks failed login
timestamps per IP. After 10 failures in a 5-minute window, the IP is locked out
for 10 minutes (HTTP 429). This state remains in memory and is lost on restart.

---

## Block/unblock lifecycle

When a task moves **into** `blocked`:
1. Frontend calls `PATCH /api/tasks/{id}` to update column.
2. A modal prompts the user for the block reason.
3. On confirm, a system comment is posted: `🚧 [BLOQUÉ le {date}] — Cause : {reason}`.
4. The block reason is stored in comments, not in the task row itself.
5. `GET /api/tasks` resolves the latest block reason inline (single SQL query per blocked task) and returns it as `block_reason` in the task object.

When a task moves **out of** `blocked`:
1. Frontend calls `PATCH /api/tasks/{id}` to update column.
2. A system comment is automatically posted: `✅ [DÉBLOQUÉ le {date}] — Déplacé vers "{column}"`.

For **bulk moves** and **stack moves**, the same comment logic runs per task without showing individual modals (a generic message is used instead).

---

## Drag & drop zones

Each card is divided into three vertical thirds during a `dragover` event:

- **Top third** → insert *before* the card (blue line indicator above)
- **Bottom third** → insert *after* the card (blue line indicator below)
- **Middle third** → *stack* the dragged card onto the target (purple dashed overlay + "📚 Empiler" label)

A leading `drop-indicator` div is always inserted before the first card in each column body, enabling drop-to-first-position.

---

## Column definitions

| ID | Label | Archivable |
|----|-------|-----------|
| `backlog` | Backlog | No |
| `todo` | To Do | No |
| `inprogress` | In Progress | No |
| `blocked` | Blocked | No |
| `done` | Done | **Yes** |
| `abandoned` | Discarded | **Yes** |

New tasks can **only** be created in `backlog`. The column selector is hidden in the "new task" modal and always submits `backlog`.

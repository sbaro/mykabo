# API Reference

All endpoints (except `POST /api/login`, `GET /`) require a valid session cookie.
Unauthorized requests return **HTTP 401**. Rate-limited IPs return **HTTP 429**.

---

## Auth

### `POST /api/login`
```json
// Request
{ "username": "admin", "password": "changeme", "remember": false }

// Response 200
{ "ok": true }
// Sets cookie: session=<token>; HttpOnly; SameSite=Lax; max_age=<ttl>
```
`remember` is optional (default `false`). When `true`, the session lasts `REMEMBER_TTL_HOURS`
(default 720 h = 30 days) instead of `SESSION_TTL_HOURS` (default 24 h).

### `POST /api/logout`
Clears the session cookie. Returns `{ "ok": true }`.

### `GET /api/me`
Returns `{ "username": "admin" }`. Used by the frontend to check auth state on load.

---

## Tasks

### `GET /api/tasks`
Returns all non-archived tasks grouped by column.

```json
{
  "backlog":    [ ...tasks ],
  "todo":       [ ...tasks ],
  "inprogress": [ ...tasks ],
  "blocked":    [ ...tasks ],  // each blocked task includes "block_reason": "..."
  "done":       [ ...tasks ],
  "abandoned":  [ ...tasks ]
}
```

Tasks are ordered by `position` within each column.
Blocked tasks have an extra `block_reason` field (latest `🚧` comment, or `null`).

### `GET /api/tasks/archived`
Returns all archived tasks ordered by `archived_at DESC`.

> ⚠️ This route must be declared **before** `GET /api/tasks/{task_id}` in the file
> to avoid FastAPI routing `"archived"` as a path parameter.

### `POST /api/tasks` → 201
```json
// Request (all fields except title optional)
{
  "title": "string",
  "description": "",
  "column": "backlog",
  "color": "#fef08a",
  "category": "",       // must be "" or match an existing category name (see /api/categories)
  "priority": "normal",   // low | normal | high
  "due_date": null,       // "YYYY-MM-DD" or null
  "recurrence": null      // "daily" | "weekly" | "monthly" | "yearly" | null
}
```
Returns the created task object. Returns **400** if `category` is non-empty and does not match
an existing entry in the `categories` table.

### `GET /api/tasks/{id}`
Returns the task with its `comments` array attached.

### `PATCH /api/tasks/{id}`
Partial update. Only fields in `_TASK_WRITABLE` are accepted:
`title`, `description`, `column`, `color`, `category`, `priority`, `due_date`, `position`, `stack_id`, `stack_pos`, `recurrence`.

`category` is validated against the `categories` table when present — send `""` to clear it, or
an existing category name to set it. Unknown names return **400**.

`recurrence` must be one of `"daily"`, `"weekly"`, `"monthly"`, `"yearly"`, or omitted/`null` to
clear it. Invalid values are silently ignored.

Returns the updated task object.

### `DELETE /api/tasks/{id}` → 204
Deletes the task and its comments (CASCADE). If the task was in a stack, the stack
is dissolved if fewer than 2 members remain.

### `POST /api/tasks/{id}/archive`
Sets `archived=1`, `archived_at=now()`, `stack_id=NULL`. Dissolves the stack if needed.

If the task has a `recurrence` value, a new task is automatically created in `backlog` with the
same title, description, color, category, priority and recurrence, and a `due_date` advanced by
one period (day / week / month / year). If the original task had no `due_date`, the new task
inherits `null`.

```json
// Response
{ "ok": true, "next_task": { ...task } }  // next_task is null when not recurring
```

### `POST /api/tasks/{id}/unarchive`
Sets `archived=0`, `archived_at=NULL`.

---

## Categories

The list of allowed `category` values is curated by the user. Tasks reference categories
by **name** (not by id) so legacy data keeps working; renaming a category propagates to
every task that uses it, and deleting one clears the field on affected tasks silently.

### `GET /api/categories`
Returns the list of categories ordered by `position` then `name`:
```json
[ { "id": 1, "name": "Dev",       "position": 0 },
  { "id": 2, "name": "Design",    "position": 1 },
  { "id": 3, "name": "Marketing", "position": 2 } ]
```

### `POST /api/categories` → 201
```json
// Request
{ "name": "Recherche" }
```
Validations: name is trimmed; non-empty; ≤ 50 chars; unique. Returns the created object.

| Status | Cause |
|--------|-------|
| 400 | Empty name or > 50 chars |
| 409 | A category with that name already exists |

### `PATCH /api/categories/{cat_id}`
```json
// Request — any subset of:
{ "name": "Dev backend", "position": 3 }
```
When `name` is changed, **all tasks** with the old name are updated to the new one in the same
transaction. Validations match `POST`.

### `DELETE /api/categories/{cat_id}` → 204
Sets `category=''` on every task that referenced this category, then deletes the row. Silent
behaviour by design — no `409` even when tasks are using the category.

### Migration
On first startup after the categories table is created, the backend seeds it from the distinct
non-empty `category` values found in existing tasks (ordered by usage, then alphabetical).

---

## Comments

### `POST /api/tasks/{id}/comments` → 201
```json
{ "content": "string" }
```
Returns the created comment object. System comments (block/unblock) are
prefixed with `🚧` or `✅` and are rendered differently in the UI (not deletable).

### `DELETE /api/tasks/{task_id}/comments/{comment_id}` → 204

---

## Stacks

### `POST /api/stacks` → 201
```json
{ "task_ids": [1, 2, 3] }  // minimum 2 ids; first = representative (stack_pos=0)
```
Assigns a shared `stack_id` to all listed tasks. Returns `{ "stack_id": "...", "tasks": [...] }`.

### `GET /api/stacks/{stack_id}`
Returns `{ "stack_id": "...", "tasks": [...] }` with comments attached to each task.
Returns **404** if the stack does not exist or has been dissolved.

### `DELETE /api/stacks/{stack_id}` → 200
Dissolves the stack: sets `stack_id=NULL`, `stack_pos=0` on all member tasks.
Tasks remain in their column. Returns `{ "ok": true }`.

### `DELETE /api/stacks/{stack_id}/tasks/{task_id}` → 200
Removes one task from the stack. If fewer than 2 members remain, the stack is
automatically dissolved. Remaining tasks are re-numbered (`stack_pos` 0, 1, 2…).

### `PATCH /api/stacks/{stack_id}/move`
Moves all tasks in the stack to a new column/position.
Only `column` and `position` are accepted (from `_STACK_MOVE_WRITABLE`).

```json
// Request
{ "column": "inprogress", "position": 0 }

// Response — when column is changed
{
  "tasks": [...],
  "prev_cols": { "42": "blocked", "43": "blocked" },  // task_id → previous column
  "new_col": "inprogress"
}
// The frontend uses prev_cols/new_col to post block/unblock comments.

// Response — position-only update
{ "tasks": [...] }
```

---

## Frontend entrypoint

### `GET /`
Returns `index.html` as `text/html`. FastAPI reads and serves the file directly.

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
  "category": "",
  "priority": "normal",   // low | normal | high
  "due_date": null        // "YYYY-MM-DD" or null
}
```
Returns the created task object.

### `GET /api/tasks/{id}`
Returns the task with its `comments` array attached.

### `PATCH /api/tasks/{id}`
Partial update. Only fields in `_TASK_WRITABLE` are accepted:
`title`, `description`, `column`, `color`, `category`, `priority`, `due_date`, `position`, `stack_id`, `stack_pos`.

Returns the updated task object.

### `DELETE /api/tasks/{id}` → 204
Deletes the task and its comments (CASCADE). If the task was in a stack, the stack
is dissolved if fewer than 2 members remain.

### `POST /api/tasks/{id}/archive`
Sets `archived=1`, `archived_at=now()`, `stack_id=NULL`. Dissolves the stack if needed.

### `POST /api/tasks/{id}/unarchive`
Sets `archived=0`, `archived_at=NULL`.

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

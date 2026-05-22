# Frontend Architecture

The entire frontend lives in `index.html` — one file, no build step, no framework.

---

## Global state

```js
tasks          // Object: { colId: [task, ...] } — mirrors GET /api/tasks response
selectedColor  // String: current color swatch selection (single task modal)
currentTaskId  // Int|null: task being edited in the modal
selectMode     // Bool: multi-select mode active
selectedIds    // Set<int>: IDs of selected tasks
bulkField      // String|null: current bulk-edit field type
browsingStackId // String|null: stack_id of the stack drawer currently open
draggedId      // Int|null: task ID being dragged (null if dragging a stack)
draggedStackId // String|null: stack_id being dragged (null if dragging a task)
draggedFromCol // String|null: source column of the current drag
stackHoverTarget // Int|null: card ID currently highlighted for stacking
pendingBlock   // {taskId, previousCol}|null: awaiting block reason input
```

---

## API layer

All fetch calls go through `apiFetch(url, opts)` which:
- Handles HTTP 401 → calls `showLogin()`
- Handles HTTP 429 → shows a rate-limit toast
- Handles non-ok responses → extracts `detail` from JSON and shows a toast
- Returns `null` for 204 No Content
- Throws on error (message is `"unauth"` or `"ratelimit"` for special cases)

Convenience wrappers: `api.get`, `api.post`, `api.patch`, `api.del`.

---

## Toast notifications

```js
toast(message, isError = false)
```

A fixed `#toast` div at the bottom of the screen. Auto-dismisses after 3 seconds.
Error toasts have a red background. Used everywhere instead of `alert()` or silent failures.

---

## Board rendering pipeline

```
loadBoard()
  └─ GET /api/tasks          (includes block_reason for blocked tasks)
  └─ renderBoard()
       └─ for each column:
            getStackRepresentatives(colTasks)
              // filters to stack_pos=0 only; hides other stack members
            for each visible task:
              makeCard(t, colId)       // standalone task
              makeStackCard(t, colId)  // stack representative
            setupColDrop(body, colId)  // attaches dragover/drop handlers
```

`loadBoard()` is the single reload function — called after every mutation.
There is no optimistic UI; the board always reflects server state.

---

## Card anatomy

### Standalone card (`makeCard`)
```
┌──────────────────────────────────┐
│ [✓ checkbox, select mode only]   │ ← .card-check (absolute, top-right)
│ Title                [✏️] [🗄️]  │ ← .card-top + .card-actions (hover)
│ [Priority tag] [Category tag]    │ ← .card-meta
│ 📅 Due date (red if overdue)     │
│ Description excerpt…             │
│ 🚧 Block reason sticker          │ ← only when column === "blocked"
└──────────────────────────────────┘
[drop-indicator div]                ← 3px blue line between cards
```

### Stack card (`makeStackCard`)
Same as above, plus:
- Wrapped in `.stack-wrapper` (CSS `::before`/`::after` pseudo-elements for stacked visual)
- `.stack-badge` (purple `×N` badge, top-left)
- `.stack-controls` row at bottom: "📋 Voir la pile (N)" + "🗂️ Désempiler"

---

## Drag & drop detail

Each column body has a leading `.drop-indicator` (enables drop-to-position-0).
Each card fragment appends a trailing `.drop-indicator`.

**`dragover` logic (per card, top-to-bottom scan):**
```
relY = e.clientY - card.getBoundingClientRect().top
third = card.height / 3

if relY <= third        → highlight indicator BEFORE card (upper third)
if relY >= 2*third      → highlight indicator AFTER card (lower third)
if third < relY < 2*third AND not self AND not selectMode
                        → stack hover: add .stack-target class to card
```

**`drop` logic:**
1. If `wasStackHover` → call `stackTwoTasks(targetId, draggedId)`
2. If `draggedStackId` → call `PATCH /api/stacks/{id}/move`, then handle block/unblock comments from `prev_cols`
3. Otherwise → normal reorder: patch column if changed, then patch positions for all cards in the target column

**`dragend`** always calls `resetDragState()` to clear `draggedId`, `draggedStackId`, `draggedFromCol` — even if the drop landed outside the board.

---

## Multi-select mode

Activated by the **☑️ Sélection** header button. In select mode:
- `.card-check` checkboxes are shown on all cards
- Clicking a card toggles its ID in `selectedIds`
- Drag & drop is disabled (`e.preventDefault()` in `dragstart`)
- The floating `.multi-toolbar` appears when `selectedIds.size > 0`

**Toolbar actions:**
- **📚 Empiler** — visible only when ≥ 2 tasks selected; calls `POST /api/stacks`
- **🎨 Couleur / 🏷️ Catégorie / ⚡ Priorité / 📅 Échéance / 📂 Colonne** — open `#bulk-overlay` modal
- **✕** — cancel selection

**Bulk apply (`applyBulk`)** patches all selected task IDs in parallel. For column moves, block/unblock system comments are posted per task.

---

## Modal conventions

| Modal / Panel | ID | Trigger |
|---------------|----|---------|
| New/edit task | `#overlay` | `openNew()` / `openEdit(id)` |
| Bulk edit | `#bulk-overlay` | `openBulkModal(field)` |
| Block reason | `#block-overlay` | `openBlockModal(taskId, prevCol)` |
| Stack browse | `#stack-panel` | `openStackPanel(stackId)` |
| Archive browse | `#archive-panel` | `openArchive()` |

All modals close on background click. Stack and archive panels are right-side drawers (`.side-panel` + `.side-drawer`).

**New task modal**: `#col-field` is hidden and `#f-col` is explicitly set to `"backlog"` before opening. The column selector is only visible when editing an existing task.

---

## Color palette

10 fixed colors available as swatches:
```js
["#fef08a","#bbf7d0","#bfdbfe","#fecaca","#e9d5ff",
 "#fed7aa","#f0fdf4","#fff","#fce7f3","#cffafe"]
```

The `darken(hex)` helper subtracts 40 from each RGB channel for the card's left border accent.

---

## System comments

Comments whose `content` starts with `🚧` or `✅` are **system comments**:
- Rendered with a yellow/amber left border and italic style (`.comment-item.system`)
- The delete button is **not** rendered for system comments
- Generated automatically by the frontend (never by the backend)

Block comment format: `🚧 [BLOQUÉ le {locale datetime}] — Cause : {reason}`
Unblock comment format: `✅ [DÉBLOQUÉ le {locale datetime}] — Déplacé vers "{column label}"`

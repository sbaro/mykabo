# Functional Specifications — MyKaBo

> MyKaBo (My Kanban Board) is a self-hosted, single-user Kanban board
> accessible from any modern browser on Mac, PC, or iPhone.

---

## 1. General principles

- **Single user**: there is exactly one account. No registration, no user management.
- **Self-hosted**: runs in a Docker container on the user's own infrastructure.
- **Persistent**: all data survives container restarts via a mounted SQLite volume.
- **Responsive**: the UI adapts to desktop and mobile viewports.
- **No data loss by default**: destructive actions (delete, archive) require explicit confirmation.

---

## 2. Authentication

### 2.1 Login
- The application is protected by a username/password login screen.
- Credentials are configured via environment variables (`KANBAN_USER`, `KANBAN_PASS`).
- On successful login, a session cookie is set (HttpOnly, SameSite=Lax).
- Sessions expire after a configurable TTL (default: 24 hours).

### 2.2 Brute-force protection
- After 10 failed login attempts within a 5-minute window, the originating IP is locked out for 10 minutes.
- Locked-out attempts return HTTP 429 with a user-facing error message.

### 2.3 Logout
- A **⏻ Déconnexion** button in the header ends the session and returns to the login screen.

---

## 3. Board layout

The board displays **six columns** in fixed left-to-right order:

| # | ID | Label | Purpose |
|---|----|-------|---------|
| 1 | `backlog` | Backlog | Ideas and future work not yet scheduled |
| 2 | `todo` | To Do | Work planned for the near future |
| 3 | `inprogress` | In Progress | Work currently being done |
| 4 | `blocked` | Blocked | Work that cannot proceed due to an impediment |
| 5 | `done` | Done | Completed work |
| 6 | `abandoned` | Discarded | Work that has been cancelled or dropped |

Each column header shows its label and a live count of the tasks it contains (including stacked tasks).

The board scrolls horizontally if it does not fit the viewport.

---

## 4. Tasks

### 4.1 Task fields

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| Title | Text | Yes | Displayed prominently on the card |
| Description | Text | No | Truncated to 80 chars on the card; full text in modal |
| Column | Enum | Yes | One of the six column IDs |
| Color | Hex color | No | Post-it background color; chosen from a 10-color palette |
| Category | Text | No | Chosen from a managed list (see § 8). Empty (“no category”) is allowed. Displayed as a tag on the card. |
| Priority | Enum | No | `low`, `normal` (default), `high`; displayed as a colored tag |
| Due date | Date | No | Displayed on card; shown in red with ⚠️ if past due and task is not done/discarded |

### 4.2 Creating a task
- Tasks can **only** be created in the **Backlog** column.
- The "+ Ajouter une tâche" button appears only at the bottom of the Backlog column.
- The creation modal does not show a column selector.

### 4.3 Editing a task
- The ✏️ button on any card opens the edit modal.
- All fields are editable, including the column (allowing manual column changes).
- Changes are saved immediately to the server on "Enregistrer".

### 4.4 Deleting a task
- Available in the edit modal via a "Supprimer" button.
- Requires confirmation. Deletion is permanent and also deletes all comments.
- If the task is part of a stack and its removal leaves fewer than 2 members, the stack is dissolved.

### 4.5 Task cards
Cards are rendered as **post-its** with:
- A left border accent (darkened variant of the card color)
- Title, priority tag, category tag
- Due date (red + ⚠️ if overdue)
- Description excerpt (up to 80 characters)
- A 🚧 block reason sticker (only when the task is in the Blocked column)
- Action buttons (edit, archive) visible on hover

---

## 5. Moving tasks

### 5.1 Drag & drop between columns
- Any task card can be dragged to another column.
- Dropping onto a column body moves the card to the bottom of that column.

### 5.2 Reordering within a column
- Cards can be dragged up or down within their column.
- A **blue horizontal line** indicates the insertion position.
- The insertion zone is the **top or bottom third** of a card.
- Drop-to-first-position is supported (indicator appears above the first card).
- The new order is persisted immediately.

### 5.3 Moving to Blocked
When a task is moved into the **Blocked** column (by drag or by editing):
1. A modal prompts the user to enter a **block reason**.
2. Cancelling reverts the task to its previous column.
3. On confirmation, the reason is saved as a system comment on the task.
4. A **🚧 sticker** showing the block reason appears on the card.

### 5.4 Moving out of Blocked
When a task leaves the **Blocked** column:
1. A system comment is automatically added: `✅ [DÉBLOQUÉ le {datetime}] — Déplacé vers "{column}"`.
2. The 🚧 sticker is removed from the card.

---

## 6. Categories

### 6.1 Curated list
- Tasks reference categories by **name** from a curated list (strict mode).
- The category dropdown in the task modal lists every category from the management drawer plus “— Aucune —”.
- The backend rejects any task create/update whose `category` is non-empty and not in the list (HTTP 400).

### 6.2 Managing the list
- The **🏷️ Catégories** button in the header opens a right-side drawer.
- The drawer lists every category with its current task count.
- Per row:
  - **✏️** opens inline edit; **✓** saves, **✕** cancels. Validations: required, ≤ 50 chars, unique.
  - **🗑️** deletes after a `confirm()` dialog. Tasks that used this category are silently left without one.
- A **“+ Ajouter”** input at the bottom of the drawer creates a new category.

### 6.3 Effects of changes
- **Renaming** a category propagates to every task using it (server-side, in the same transaction).
- **Deleting** a category clears the field on every task that used it (no reassignment prompt).
- Renames and deletions trigger a full board reload so card tags and filter chips reflect the change.
- The bulk-edit “🏷️ Catégorie” action also surfaces the curated list with an explicit “— Aucune (effacer le champ) —” option to clear the field on selected tasks.

### 6.4 Initial population
- On first startup the backend extracts the distinct non-empty `category` values from existing tasks and inserts them into the new table, ordered by usage descending.

---

## 7. Filtering the board

### 7.1 Filter bar
- Located under the header, sticky during horizontal scroll.
- In **compact mode** (default), the bar shows a single **🔍 Filtres** button until the user expands it; when filters are active it stays expanded and a badge shows the active filter count.
- Two groups of toggleable chips:
  - **⚡ Priorité** — Haute, Normale, Basse, with their priority colours.
  - **🏷️ Catégorie** — one chip per category that is currently in use on the board, sorted by frequency.
- Each chip displays a count of matching tasks.

### 7.2 Logic
- Within a group: **OR** (any selected chip matches).
- Between groups: **AND** (selected priorities AND selected categories must both match).
- An **↺ Tout effacer** button clears all active filters at once.

### 7.3 Behaviour
- Non-matching cards are **dimmed** (greyscaled, opacity .18) rather than hidden, preserving the overall layout and stack counts.
- Each column header badge shows `matches/total` while filters are active.
- Filters apply to the representative of a stack; a stack stays visible if **any** member matches.

---

## 8. Comments

- Each task can have an unlimited number of comments.
- Comments are visible in the edit modal, ordered chronologically.
- Comments are added via a text input at the bottom of the comment list.
- User comments can be deleted individually.
- **System comments** (block/unblock events) are visually distinct (amber left border, italic) and **cannot be deleted**.
- System comment prefixes: `🚧` for block events, `✅` for unblock events.

---

## 9. Archiving

### 9.1 Archiving a single task
- Available only for tasks in the **Done** or **Discarded** columns.
- Accessible via the 🗄️ button on the card or in the edit modal.
- Requires confirmation.
- Archived tasks disappear from the board but are not deleted.

### 9.2 Archiving an entire column
- A **"🗄️ Tout archiver (N)"** button appears at the bottom of Done and Discarded columns when they contain tasks.
- Archives all tasks in the column in one action. Requires confirmation.

### 9.3 Archive drawer
- Accessible via the **"🗄️ Archives"** button in the header.
- Opens as a right-side drawer listing all archived tasks, grouped by their original column.
- Each archived task shows its title, archive date, and category.
- Actions per archived task:
  - **↩️ Restaurer**: moves the task back to its original column as an active task.
  - **🗑️**: permanently deletes the task and all its comments. Requires confirmation.

---

## 10. Stacks

A **stack** is a visual and logical grouping of two or more tasks displayed as a
single layered card on the board.

### 10.1 Visual representation
- Stacked cards appear as a deck with two ghost cards peeking behind the representative card.
- A purple **×N** badge in the top-left corner indicates the number of tasks in the stack.
- Only the **representative task** (first task added, `stack_pos=0`) is visible on the board.

### 10.2 Creating a stack — by drag & drop
- Drag one card onto the **middle third** of another card.
- A purple dashed overlay with "📚 Empiler" appears on the target card to confirm intent.
- Releasing the drag creates the stack. The target card becomes the representative.
- An existing stack can absorb a new card by dragging onto it.
- Two existing stacks can be merged by dragging one onto the other.

### 10.3 Creating a stack — by selection
- Enter selection mode (☑️ Sélection), select 2 or more tasks, then click **📚 Empiler** in the floating toolbar.
- The first selected task becomes the representative.

### 10.4 Browsing a stack
- Click **"📋 Voir la pile (N)"** on a stack card to open the stack drawer.
- The drawer lists all tasks in the stack with their priority, category, due date, and description excerpt.
- The representative is labelled **Rep.**
- Each task has:
  - ✏️ button: closes the drawer and opens the task's edit modal.
  - **↗️ Éjecter**: removes the task from the stack; it remains in the same column as a standalone card. If only 1 task remains, the stack is automatically dissolved.

### 10.5 Moving a stack
- Dragging the representative card moves **all tasks** in the stack to the target column simultaneously.
- Block/unblock system comments are posted for each affected task as appropriate.

### 10.6 Archiving a stack
- The 🗄️ button on a stack card archives **all tasks** in the stack at once. Requires confirmation.

### 10.7 Unstacking
- **"🗂️ Désempiler"** on the stack card, or **"🗂️ Désempiler toutes les tâches"** in the stack drawer.
- Dissolves the stack; all tasks remain in their column as independent cards.
- Requires confirmation.

### 10.8 Stack integrity rules
- A stack must always contain at least 2 tasks. Any operation that would leave 1 member automatically dissolves the stack.
- Archived tasks are removed from their stack (`stack_id` set to NULL). If this leaves fewer than 2 active members, the stack is dissolved.
- Deleting a task from a stack follows the same rule.

---

## 11. Multi-select mode

### 11.1 Activating selection mode
- Click **☑️ Sélection** in the header to toggle selection mode on/off.
- The button turns purple when active.
- Drag & drop is disabled while in selection mode.

### 11.2 Selecting tasks
- In selection mode, clicking any card toggles its selection.
- Selected cards display a purple ✓ checkbox badge and a purple outline.
- Clicking the card-actions area (edit button) does not trigger selection.

### 11.3 Bulk action toolbar
A floating toolbar appears at the bottom of the screen whenever at least one task is selected. It shows the count of selected tasks and the following actions:

| Button | Action |
|--------|--------|
| 📚 Empiler | Create a stack from selected tasks (requires ≥ 2) |
| 🎨 Couleur | Change the post-it color of all selected tasks |
| 🏷️ Catégorie | Set the category of all selected tasks |
| ⚡ Priorité | Set the priority of all selected tasks |
| 📅 Échéance | Set the due date of all selected tasks |
| 📂 Colonne | Move all selected tasks to a chosen column |
| ✕ | Cancel selection mode without making changes |

### 11.4 Bulk edit modal
Each bulk action (except Stack and Cancel) opens a focused modal with a single field. Leaving the field empty (where applicable) cancels without making changes.

### 11.5 Bulk column move
When moving selected tasks to **Blocked**, a generic system comment is added automatically to each task (no individual block-reason modals). When moving tasks out of **Blocked**, unblock comments are posted automatically.

---

## 12. UI feedback & error handling

- All API errors surface as **toast notifications** (bottom of screen, auto-dismiss after 3 seconds). Error toasts have a red background.
- HTTP 401 (session expired) automatically redirects to the login screen.
- HTTP 429 (rate limit) displays a specific message on the login screen.
- Destructive actions (delete, archive, unstack) always require a browser `confirm()` dialog.
- The board always reflects the latest server state (full reload after every mutation).

---

## 13. Out of scope (current version)

The following features have been explicitly **not implemented**:

- Multiple user accounts or shared access
- Real-time collaboration / WebSockets
- Drag & drop on touch/mobile devices (mobile users must use the edit modal to move tasks)
- Subtasks or task dependencies
- File attachments
- Email or push notifications
- Free-text search
- Per-category colours
- Drag-to-reorder of the categories list
- Multiple labels per task (still one `category` field)
- Recurring tasks
- Time tracking
- Swimlanes
- Custom column definitions

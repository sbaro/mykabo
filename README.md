# MyKaBo — My Kanban Board

A self-hosted, single-user Kanban board for personal task management. Lightweight, no external services required — just Docker.

## Features

- **6 columns**: Backlog, To Do, In Progress, Blocked, Done, Abandoned
- **Drag-and-drop** cards between columns
- **Rich task metadata**: title, description, color, category, priority, due date
- **Recurring tasks**: daily, weekly, monthly, yearly
- **Task stacking**: group related cards into a single representative card
- **Block/unblock system**: mark tasks as blocked with a reason; status changes are automatically logged as comments
- **Comments**: free-form notes plus automatic system notifications
- **Archive**: completed or discarded tasks can be archived and restored
- **WIP limits**: set work-in-progress limits per column
- **Categories**: custom labels with user-defined ordering
- **Persistent sessions**: survive container restarts; optional "remember me" (30 days)
- **Brute-force protection**: IP-based rate limiting on login (10 attempts / 5 min)

## Requirements

- [Docker](https://docs.docker.com/get-docker/) with the Compose plugin (v2)

## Quick Start

```bash
git clone https://github.com/sbaro/mykabo.git
cd mykabo
docker compose up -d
```

Then open http://localhost:8000 in your browser.

**Default credentials**: `admin` / `changeme`  
Change them immediately via *Settings* in the app, or via the environment variables below.

To stop the app:

```bash
docker compose down
```

Data is stored in `./kanban_data/` and persists across restarts.

## Configuration

All settings are passed as environment variables. Edit `compose.yml` to override the defaults.

| Variable | Default | Description |
|---|---|---|
| `KANBAN_USER` | `admin` | Login username |
| `KANBAN_PASS` | `changeme` | Login password (hashed at startup) |
| `SESSION_TTL_HOURS` | `24` | Session lifetime in hours |
| `REMEMBER_TTL_HOURS` | `720` | "Remember me" session lifetime (30 days) |
| `ALLOWED_ORIGINS` | `http://localhost:8000` | Comma-separated CORS whitelist |
| `DB_PATH` | `/data/kanban.db` | Path to the SQLite database inside the container |

Example `compose.yml` override:

```yaml
services:
  kanban:
    build: .
    container_name: kanban
    restart: unless-stopped
    ports:
      - "80:8000"
    volumes:
      - ./kanban_data:/data
    environment:
      - KANBAN_USER=alice
      - KANBAN_PASS=my-secret-password
      - SESSION_TTL_HOURS=48
```

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, Uvicorn |
| Database | SQLite (WAL mode) |
| Frontend | Vanilla HTML / CSS / JavaScript (no build step) |
| Container | Docker, Docker Compose |

## License

This project is licensed under the [GNU General Public License v2.0](LICENSE).

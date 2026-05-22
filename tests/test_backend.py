"""
Backend tests for MyKaBo (FastAPI + SQLite).

Run from the project root:
    pytest tests/test_backend.py -v

conftest.py must set DB_PATH / KANBAN_USER / KANBAN_PASS *before* this module
is imported, so main.py reads the right values at module level.
"""
import time
import re

import pytest
from starlette.testclient import TestClient

import main  # imported after conftest.py has set env vars


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _max_age(response) -> int | None:
    """Extract Max-Age value from a Set-Cookie header."""
    m = re.search(r"[Mm]ax-[Aa]ge=(\d+)", response.headers.get("set-cookie", ""))
    return int(m.group(1)) if m else None


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_db():
    """Wipe all rows and reset brute-force state before every test."""
    conn = main.get_db()
    conn.execute("DELETE FROM comments")
    conn.execute("DELETE FROM tasks")
    conn.execute("DELETE FROM sessions")
    conn.execute("DELETE FROM categories")
    conn.commit()
    conn.close()
    main._failed.clear()


@pytest.fixture
def client():
    return TestClient(main.app, raise_server_exceptions=True)


@pytest.fixture
def auth(client):
    """TestClient already logged in."""
    r = client.post("/api/login", json={"username": "admin", "password": "changeme"})
    assert r.status_code == 200
    return client


@pytest.fixture
def task(auth):
    """One task pre-created in backlog."""
    r = auth.post("/api/tasks", json={"title": "T1", "column": "backlog"})
    assert r.status_code == 201
    return r.json()


# ─── Auth ─────────────────────────────────────────────────────────────────────

class TestAuth:

    def test_login_success(self, client):
        r = client.post("/api/login", json={"username": "admin", "password": "changeme"})
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        assert "session" in r.cookies

    def test_login_wrong_password(self, client):
        r = client.post("/api/login", json={"username": "admin", "password": "badpass"})
        assert r.status_code == 401

    def test_login_wrong_username(self, client):
        r = client.post("/api/login", json={"username": "hacker", "password": "changeme"})
        assert r.status_code == 401

    def test_login_remember_me_sets_longer_session(self, client):
        r = client.post(
            "/api/login",
            json={"username": "admin", "password": "changeme", "remember": True},
        )
        assert r.status_code == 200
        # Session in DB should expire well beyond the default short TTL
        conn = main.get_db()
        row = conn.execute("SELECT expires_at FROM sessions LIMIT 1").fetchone()
        conn.close()
        assert row["expires_at"] - time.time() > main.SESSION_TTL * 3600

    def test_login_default_session_shorter_than_remember(self, client):
        r_short = client.post(
            "/api/login",
            json={"username": "admin", "password": "changeme", "remember": False},
        )
        short_age = _max_age(r_short)
        client.post("/api/logout")
        r_long = client.post(
            "/api/login",
            json={"username": "admin", "password": "changeme", "remember": True},
        )
        long_age = _max_age(r_long)
        assert short_age is not None and long_age is not None
        assert long_age > short_age

    def test_logout_invalidates_session(self, auth):
        auth.post("/api/logout")
        r = auth.get("/api/me")
        assert r.status_code == 401

    def test_me_authenticated(self, auth):
        r = auth.get("/api/me")
        assert r.status_code == 200
        assert r.json()["username"] == "admin"

    def test_me_unauthenticated(self, client):
        r = client.get("/api/me")
        assert r.status_code == 401

    def test_brute_force_lockout_after_ten_failures(self, client):
        for _ in range(10):
            client.post("/api/login", json={"username": "admin", "password": "bad"})
        r = client.post("/api/login", json={"username": "admin", "password": "bad"})
        assert r.status_code == 429

    def test_brute_force_reset_on_success(self, client):
        # 9 bad attempts then 1 good — counter should clear
        for _ in range(9):
            client.post("/api/login", json={"username": "admin", "password": "bad"})
        r = client.post("/api/login", json={"username": "admin", "password": "changeme"})
        assert r.status_code == 200
        # One more bad attempt is allowed (counter was cleared)
        r2 = client.post("/api/login", json={"username": "admin", "password": "bad"})
        assert r2.status_code == 401  # not 429


# ─── Tasks ────────────────────────────────────────────────────────────────────

class TestTasks:

    def test_create_task(self, auth):
        r = auth.post("/api/tasks", json={"title": "My Task", "column": "backlog"})
        assert r.status_code == 201
        d = r.json()
        assert d["title"] == "My Task"
        assert d["column"] == "backlog"
        assert d["priority"] == "normal"
        assert d["archived"] == 0

    def test_create_task_requires_auth(self, client):
        r = client.post("/api/tasks", json={"title": "Sneaky"})
        assert r.status_code == 401

    def test_get_tasks_contains_all_columns(self, auth):
        r = auth.get("/api/tasks")
        assert r.status_code == 200
        for col in ("backlog", "todo", "inprogress", "blocked", "done", "abandoned"):
            assert col in r.json()

    def test_get_tasks_places_task_in_correct_column(self, auth, task):
        board = auth.get("/api/tasks").json()
        assert any(t["id"] == task["id"] for t in board["backlog"])

    def test_get_tasks_excludes_archived(self, auth, task):
        auth.post(f"/api/tasks/{task['id']}/archive")
        board = auth.get("/api/tasks").json()
        ids = [t["id"] for col in board.values() for t in col]
        assert task["id"] not in ids

    def test_get_task_by_id_includes_comments(self, auth, task):
        r = auth.get(f"/api/tasks/{task['id']}")
        assert r.status_code == 200
        d = r.json()
        assert d["id"] == task["id"]
        assert "comments" in d

    def test_get_task_not_found(self, auth):
        assert auth.get("/api/tasks/99999").status_code == 404

    def test_get_archived_tasks(self, auth, task):
        auth.post(f"/api/tasks/{task['id']}/archive")
        r = auth.get("/api/tasks/archived")
        assert r.status_code == 200
        assert any(t["id"] == task["id"] for t in r.json())

    def test_update_task_title(self, auth, task):
        r = auth.patch(f"/api/tasks/{task['id']}", json={"title": "Updated"})
        assert r.status_code == 200
        assert r.json()["title"] == "Updated"

    def test_update_task_column(self, auth, task):
        r = auth.patch(f"/api/tasks/{task['id']}", json={"column": "todo"})
        assert r.status_code == 200
        assert r.json()["column"] == "todo"

    def test_update_task_non_writable_field_ignored(self, auth, task):
        # 'created_at' is not in _TASK_WRITABLE — silently ignored
        r = auth.patch(
            f"/api/tasks/{task['id']}",
            json={"title": "OK", "created_at": "1970-01-01"},
        )
        assert r.status_code == 200
        d = r.json()
        assert d["title"] == "OK"
        assert d["created_at"] != "1970-01-01"

    def test_delete_task(self, auth, task):
        r = auth.delete(f"/api/tasks/{task['id']}")
        assert r.status_code == 204
        assert auth.get(f"/api/tasks/{task['id']}").status_code == 404

    def test_archive_task(self, auth, task):
        r = auth.post(f"/api/tasks/{task['id']}/archive")
        assert r.status_code == 200
        # Directly check DB
        conn = main.get_db()
        row = conn.execute(
            "SELECT archived FROM tasks WHERE id=?", (task["id"],)
        ).fetchone()
        conn.close()
        assert row["archived"] == 1

    def test_unarchive_task(self, auth, task):
        auth.post(f"/api/tasks/{task['id']}/archive")
        r = auth.post(f"/api/tasks/{task['id']}/unarchive")
        assert r.status_code == 200
        board = auth.get("/api/tasks").json()
        ids = [t["id"] for col in board.values() for t in col]
        assert task["id"] in ids

    def test_block_reason_resolved_in_get_tasks(self, auth):
        t = auth.post("/api/tasks", json={"title": "Blocker", "column": "blocked"}).json()
        auth.post(
            f"/api/tasks/{t['id']}/comments",
            json={"content": "🚧 [BLOQUÉ le 22/05/2026 10:00:00] — Cause : Serveur HS"},
        )
        board = auth.get("/api/tasks").json()
        bt = next(x for x in board["blocked"] if x["id"] == t["id"])
        assert bt.get("block_reason") == "Serveur HS"

    def test_block_reason_absent_when_no_comment(self, auth):
        t = auth.post("/api/tasks", json={"title": "B", "column": "blocked"}).json()
        board = auth.get("/api/tasks").json()
        bt = next(x for x in board["blocked"] if x["id"] == t["id"])
        assert bt.get("block_reason") is None

    def test_block_reason_uses_latest_comment(self, auth):
        t = auth.post("/api/tasks", json={"title": "B", "column": "blocked"}).json()
        auth.post(
            f"/api/tasks/{t['id']}/comments",
            json={"content": "🚧 [BLOQUÉ le 01/01/2025 09:00:00] — Cause : Ancienne raison"},
        )
        auth.post(
            f"/api/tasks/{t['id']}/comments",
            json={"content": "🚧 [BLOQUÉ le 22/05/2026 10:00:00] — Cause : Nouvelle raison"},
        )
        board = auth.get("/api/tasks").json()
        bt = next(x for x in board["blocked"] if x["id"] == t["id"])
        assert bt.get("block_reason") == "Nouvelle raison"


# ─── Comments ─────────────────────────────────────────────────────────────────

class TestComments:

    def test_add_comment(self, auth, task):
        r = auth.post(
            f"/api/tasks/{task['id']}/comments",
            json={"content": "Hello!"},
        )
        assert r.status_code == 201
        assert r.json()["content"] == "Hello!"

    def test_add_comment_unknown_task(self, auth):
        r = auth.post("/api/tasks/99999/comments", json={"content": "Hi"})
        assert r.status_code == 404

    def test_delete_comment(self, auth, task):
        c = auth.post(
            f"/api/tasks/{task['id']}/comments",
            json={"content": "To delete"},
        ).json()
        r = auth.delete(f"/api/tasks/{task['id']}/comments/{c['id']}")
        assert r.status_code == 204

    def test_comments_cascade_on_task_delete(self, auth, task):
        auth.post(f"/api/tasks/{task['id']}/comments", json={"content": "Will vanish"})
        auth.delete(f"/api/tasks/{task['id']}")
        conn = main.get_db()
        count = conn.execute(
            "SELECT COUNT(*) FROM comments WHERE task_id=?", (task["id"],)
        ).fetchone()[0]
        conn.close()
        assert count == 0

    def test_get_task_includes_comments(self, auth, task):
        auth.post(f"/api/tasks/{task['id']}/comments", json={"content": "Note"})
        r = auth.get(f"/api/tasks/{task['id']}")
        assert len(r.json()["comments"]) == 1
        assert r.json()["comments"][0]["content"] == "Note"


# ─── Stacks ───────────────────────────────────────────────────────────────────

class TestStacks:

    def _tasks(self, auth, n=2, col="backlog"):
        return [
            auth.post("/api/tasks", json={"title": f"T{i}", "column": col}).json()
            for i in range(n)
        ]

    def test_create_stack(self, auth):
        a, b = self._tasks(auth)
        r = auth.post("/api/stacks", json={"task_ids": [a["id"], b["id"]]})
        assert r.status_code == 201
        d = r.json()
        assert "stack_id" in d
        assert len(d["tasks"]) == 2

    def test_create_stack_needs_at_least_two_tasks(self, auth):
        a = self._tasks(auth, n=1)[0]
        r = auth.post("/api/stacks", json={"task_ids": [a["id"]]})
        assert r.status_code == 400

    def test_create_stack_representative_column_wins(self, auth):
        """When stacking tasks from different columns, all move to the representative's column."""
        a = auth.post("/api/tasks", json={"title": "Rep", "column": "todo"}).json()
        b = auth.post("/api/tasks", json={"title": "Other", "column": "inprogress"}).json()
        r = auth.post("/api/stacks", json={"task_ids": [a["id"], b["id"]]})
        assert r.status_code == 201
        for t in r.json()["tasks"]:
            assert t["column"] == "todo"

    def test_stack_positions_are_sequential(self, auth):
        a, b = self._tasks(auth)
        tasks = auth.post("/api/stacks", json={"task_ids": [a["id"], b["id"]]}).json()["tasks"]
        pos = {t["id"]: t["stack_pos"] for t in tasks}
        assert pos[a["id"]] == 0  # representative
        assert pos[b["id"]] == 1

    def test_get_stack(self, auth):
        a, b = self._tasks(auth)
        sid = auth.post("/api/stacks", json={"task_ids": [a["id"], b["id"]]}).json()["stack_id"]
        r = auth.get(f"/api/stacks/{sid}")
        assert r.status_code == 200
        assert r.json()["stack_id"] == sid
        assert len(r.json()["tasks"]) == 2

    def test_get_stack_not_found(self, auth):
        assert auth.get("/api/stacks/doesnotexist").status_code == 404

    def test_unstack_dissolves_stack(self, auth):
        a, b = self._tasks(auth)
        sid = auth.post("/api/stacks", json={"task_ids": [a["id"], b["id"]]}).json()["stack_id"]
        r = auth.delete(f"/api/stacks/{sid}")
        assert r.status_code == 200
        # Both tasks should have no stack_id
        for tid in (a["id"], b["id"]):
            assert auth.get(f"/api/tasks/{tid}").json()["stack_id"] is None

    def test_remove_task_from_three_member_stack_renumbers(self, auth):
        a, b, c = self._tasks(auth, n=3)
        sid = auth.post(
            "/api/stacks", json={"task_ids": [a["id"], b["id"], c["id"]]}
        ).json()["stack_id"]
        auth.delete(f"/api/stacks/{sid}/tasks/{b['id']}")
        remaining = auth.get(f"/api/stacks/{sid}").json()["tasks"]
        assert len(remaining) == 2
        assert {t["stack_pos"] for t in remaining} == {0, 1}

    def test_remove_task_dissolves_two_member_stack(self, auth):
        a, b = self._tasks(auth)
        sid = auth.post("/api/stacks", json={"task_ids": [a["id"], b["id"]]}).json()["stack_id"]
        auth.delete(f"/api/stacks/{sid}/tasks/{b['id']}")
        assert auth.get(f"/api/stacks/{sid}").status_code == 404

    def test_move_stack_to_new_column_returns_prev_cols(self, auth):
        a, b = self._tasks(auth, col="backlog")
        sid = auth.post("/api/stacks", json={"task_ids": [a["id"], b["id"]]}).json()["stack_id"]
        r = auth.patch(f"/api/stacks/{sid}/move", json={"column": "done", "position": 0})
        assert r.status_code == 200
        d = r.json()
        assert d["new_col"] == "done"
        assert str(a["id"]) in d["prev_cols"]
        assert str(b["id"]) in d["prev_cols"]
        for t in d["tasks"]:
            assert t["column"] == "done"

    def test_move_stack_position_only_returns_no_prev_cols(self, auth):
        a, b = self._tasks(auth)
        sid = auth.post("/api/stacks", json={"task_ids": [a["id"], b["id"]]}).json()["stack_id"]
        r = auth.patch(f"/api/stacks/{sid}/move", json={"position": 5})
        assert r.status_code == 200
        d = r.json()
        assert "prev_cols" not in d
        assert "tasks" in d

    def test_archive_member_dissolves_stack_when_only_one_left(self, auth):
        a, b = self._tasks(auth)
        auth.post("/api/stacks", json={"task_ids": [a["id"], b["id"]]})
        auth.post(f"/api/tasks/{b['id']}/archive")
        ta = auth.get(f"/api/tasks/{a['id']}").json()
        assert ta["stack_id"] is None

    def test_delete_member_dissolves_stack_when_only_one_left(self, auth):
        a, b = self._tasks(auth)
        auth.post("/api/stacks", json={"task_ids": [a["id"], b["id"]]})
        auth.delete(f"/api/tasks/{b['id']}")
        ta = auth.get(f"/api/tasks/{a['id']}").json()
        assert ta["stack_id"] is None


# ─── Categories ───────────────────────────────────────────────────────────────

class TestCategories:

    def test_create_category(self, auth):
        r = auth.post("/api/categories", json={"name": "Frontend"})
        assert r.status_code == 201
        assert r.json()["name"] == "Frontend"

    def test_create_duplicate_category_returns_409(self, auth):
        auth.post("/api/categories", json={"name": "Ops"})
        r = auth.post("/api/categories", json={"name": "Ops"})
        assert r.status_code == 409

    def test_create_category_blank_name_returns_400(self, auth):
        r = auth.post("/api/categories", json={"name": "   "})
        assert r.status_code == 400

    def test_create_category_name_too_long_returns_400(self, auth):
        r = auth.post("/api/categories", json={"name": "A" * 51})
        assert r.status_code == 400

    def test_list_categories(self, auth):
        auth.post("/api/categories", json={"name": "Alpha"})
        auth.post("/api/categories", json={"name": "Beta"})
        r = auth.get("/api/categories")
        assert r.status_code == 200
        names = [c["name"] for c in r.json()]
        assert "Alpha" in names and "Beta" in names

    def test_task_with_unknown_category_rejected(self, auth):
        r = auth.post("/api/tasks", json={"title": "T", "category": "Unknown"})
        assert r.status_code == 400

    def test_task_with_known_category_accepted(self, auth):
        auth.post("/api/categories", json={"name": "Known"})
        r = auth.post("/api/tasks", json={"title": "T", "category": "Known"})
        assert r.status_code == 201
        assert r.json()["category"] == "Known"

    def test_rename_category_propagates_to_tasks(self, auth):
        cat = auth.post("/api/categories", json={"name": "OldName"}).json()
        t = auth.post("/api/tasks", json={"title": "T", "category": "OldName"}).json()
        auth.patch(f"/api/categories/{cat['id']}", json={"name": "NewName"})
        assert auth.get(f"/api/tasks/{t['id']}").json()["category"] == "NewName"

    def test_delete_category_clears_tasks(self, auth):
        cat = auth.post("/api/categories", json={"name": "ToDelete"}).json()
        t = auth.post("/api/tasks", json={"title": "T", "category": "ToDelete"}).json()
        r = auth.delete(f"/api/categories/{cat['id']}")
        assert r.status_code == 204
        assert auth.get(f"/api/tasks/{t['id']}").json()["category"] == ""

    def test_update_category_not_found(self, auth):
        r = auth.patch("/api/categories/99999", json={"name": "X"})
        assert r.status_code == 404


# ─── WIP Limits ───────────────────────────────────────────────────────────────

class TestWipLimits:

    def test_get_wip_limits_empty(self, auth):
        r = auth.get("/api/wip_limits")
        assert r.status_code == 200
        assert r.json() == {}

    def test_set_wip_limit(self, auth):
        r = auth.patch("/api/wip_limits/inprogress", json={"max_tasks": 3})
        assert r.status_code == 200
        assert r.json()["inprogress"] == 3

    def test_set_wip_limit_unknown_column(self, auth):
        r = auth.patch("/api/wip_limits/nonexistent", json={"max_tasks": 3})
        assert r.status_code == 400

    def test_remove_wip_limit_with_null(self, auth):
        auth.patch("/api/wip_limits/inprogress", json={"max_tasks": 3})
        r = auth.patch("/api/wip_limits/inprogress", json={"max_tasks": None})
        assert r.status_code == 200
        assert "inprogress" not in r.json()

    def test_remove_wip_limit_with_zero(self, auth):
        auth.patch("/api/wip_limits/todo", json={"max_tasks": 5})
        r = auth.patch("/api/wip_limits/todo", json={"max_tasks": 0})
        assert r.status_code == 200
        assert "todo" not in r.json()

    def test_multiple_columns_can_have_limits(self, auth):
        auth.patch("/api/wip_limits/todo", json={"max_tasks": 4})
        auth.patch("/api/wip_limits/inprogress", json={"max_tasks": 2})
        r = auth.get("/api/wip_limits")
        d = r.json()
        assert d["todo"] == 4
        assert d["inprogress"] == 2

    def test_overwrite_existing_limit(self, auth):
        auth.patch("/api/wip_limits/todo", json={"max_tasks": 5})
        r = auth.patch("/api/wip_limits/todo", json={"max_tasks": 10})
        assert r.json()["todo"] == 10

    def test_wip_limit_requires_auth(self, client):
        r = client.get("/api/wip_limits")
        assert r.status_code == 401

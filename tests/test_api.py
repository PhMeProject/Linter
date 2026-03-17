"""
Tests for the Flask API routes and SQLite template persistence.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def app(tmp_path, monkeypatch):
    """Return a Flask test app wired to an isolated in-memory SQLite DB."""
    # Point DATABASE_PATH at a temp file so tests never touch the real DB.
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))

    # Re-import app so _resolve_db_path() picks up the env var.
    import importlib
    import app as app_module
    importlib.reload(app_module)

    app_module.app.config["TESTING"] = True
    return app_module.app


@pytest.fixture()
def client(app):
    return app.test_client()


# Minimal typography payload that the JS settings page sends.
def _typo(**overrides):
    base = {
        "h1":       {"font_family": None, "font_size": None, "font_color": None, "bold": None, "italic": None},
        "body":     {"font_family": None, "font_size": None, "font_color": None, "bold": None, "italic": None},
        "captions": {"font_family": None, "font_size": None, "font_color": None, "bold": None, "italic": None},
    }
    base.update(overrides)
    return base


def _payload(name="My Brand", **typo_overrides):
    return {"template_name": name, "typography": _typo(**typo_overrides)}


# ---------------------------------------------------------------------------
# POST /api/templates  – create
# ---------------------------------------------------------------------------

class TestCreateTemplate:
    def test_returns_201_with_id_and_name(self, client):
        r = client.post("/api/templates",
                        data=json.dumps(_payload("Acme")),
                        content_type="application/json")
        assert r.status_code == 201
        body = r.get_json()
        assert "id" in body
        assert body["template_name"] == "Acme"

    def test_id_is_uuid(self, client):
        r = client.post("/api/templates",
                        data=json.dumps(_payload()),
                        content_type="application/json")
        import re
        assert re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            r.get_json()["id"]
        )

    def test_rejects_missing_body(self, client):
        r = client.post("/api/templates", content_type="application/json")
        assert r.status_code == 400

    def test_rejects_non_dict_body(self, client):
        r = client.post("/api/templates",
                        data=json.dumps([1, 2, 3]),
                        content_type="application/json")
        assert r.status_code == 400

    def test_saves_custom_typography(self, client):
        r = client.post("/api/templates", content_type="application/json",
                        data=json.dumps(_payload("Brand", h1={
                            "font_family": "Arial", "font_size": 16,
                            "font_color": "#FF0000", "bold": True, "italic": None,
                        })))
        assert r.status_code == 201
        tid = r.get_json()["id"]
        # Round-trip: GET should return the same values.
        r2 = client.get(f"/api/templates/{tid}")
        typo = r2.get_json()["typography"]
        assert typo["h1"]["font_family"] == "Arial"
        assert typo["h1"]["font_size"] == 16
        assert typo["h1"]["bold"] is True

    def test_empty_template_name_allowed(self, client):
        r = client.post("/api/templates",
                        data=json.dumps({"template_name": "", "typography": {}}),
                        content_type="application/json")
        assert r.status_code == 201


# ---------------------------------------------------------------------------
# GET /api/templates  – list
# ---------------------------------------------------------------------------

class TestListTemplates:
    def test_empty_list_initially(self, client):
        r = client.get("/api/templates")
        assert r.status_code == 200
        assert r.get_json() == []

    def test_lists_created_templates(self, client):
        client.post("/api/templates", content_type="application/json",
                    data=json.dumps(_payload("Alpha")))
        client.post("/api/templates", content_type="application/json",
                    data=json.dumps(_payload("Beta")))
        r = client.get("/api/templates")
        names = [t["template_name"] for t in r.get_json()]
        assert "Alpha" in names
        assert "Beta" in names

    def test_list_items_have_id_and_name(self, client):
        client.post("/api/templates", content_type="application/json",
                    data=json.dumps(_payload("X")))
        items = client.get("/api/templates").get_json()
        assert len(items) == 1
        assert "id" in items[0]
        assert "template_name" in items[0]


# ---------------------------------------------------------------------------
# GET /api/templates/<tid>  – fetch single
# ---------------------------------------------------------------------------

class TestGetTemplate:
    def test_returns_template(self, client):
        tid = client.post("/api/templates", content_type="application/json",
                          data=json.dumps(_payload("Zeta"))).get_json()["id"]
        r = client.get(f"/api/templates/{tid}")
        assert r.status_code == 200
        body = r.get_json()
        assert body["id"] == tid
        assert body["template_name"] == "Zeta"
        assert "typography" in body

    def test_404_for_unknown_id(self, client):
        r = client.get("/api/templates/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 404

    def test_typography_roundtrip_all_defaults(self, client):
        """All-null section fields should come back as-is."""
        tid = client.post("/api/templates", content_type="application/json",
                          data=json.dumps(_payload())).get_json()["id"]
        typo = client.get(f"/api/templates/{tid}").get_json()["typography"]
        assert typo["h1"]["font_family"] is None
        assert typo["body"]["bold"] is None


# ---------------------------------------------------------------------------
# PUT /api/templates/<tid>  – update
# ---------------------------------------------------------------------------

class TestUpdateTemplate:
    def test_update_name(self, client):
        tid = client.post("/api/templates", content_type="application/json",
                          data=json.dumps(_payload("Old"))).get_json()["id"]
        r = client.put(f"/api/templates/{tid}",
                       content_type="application/json",
                       data=json.dumps(_payload("New")))
        assert r.status_code == 200
        assert client.get(f"/api/templates/{tid}").get_json()["template_name"] == "New"

    def test_update_typography(self, client):
        tid = client.post("/api/templates", content_type="application/json",
                          data=json.dumps(_payload())).get_json()["id"]
        client.put(f"/api/templates/{tid}", content_type="application/json",
                   data=json.dumps(_payload(h1={
                       "font_family": "Georgia", "font_size": None,
                       "font_color": None, "bold": None, "italic": True,
                   })))
        typo = client.get(f"/api/templates/{tid}").get_json()["typography"]
        assert typo["h1"]["font_family"] == "Georgia"
        assert typo["h1"]["italic"] is True

    def test_404_for_unknown_id(self, client):
        r = client.put("/api/templates/00000000-0000-0000-0000-000000000000",
                       content_type="application/json",
                       data=json.dumps(_payload()))
        assert r.status_code == 404

    def test_rejects_bad_body(self, client):
        tid = client.post("/api/templates", content_type="application/json",
                          data=json.dumps(_payload())).get_json()["id"]
        r = client.put(f"/api/templates/{tid}",
                       content_type="application/json",
                       data=json.dumps("not a dict"))
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# DELETE /api/templates/<tid>
# ---------------------------------------------------------------------------

class TestDeleteTemplate:
    def test_delete_removes_template(self, client):
        tid = client.post("/api/templates", content_type="application/json",
                          data=json.dumps(_payload())).get_json()["id"]
        r = client.delete(f"/api/templates/{tid}")
        assert r.status_code == 200
        assert client.get(f"/api/templates/{tid}").status_code == 404

    def test_delete_removes_from_list(self, client):
        tid = client.post("/api/templates", content_type="application/json",
                          data=json.dumps(_payload("Gone"))).get_json()["id"]
        client.delete(f"/api/templates/{tid}")
        ids = [t["id"] for t in client.get("/api/templates").get_json()]
        assert tid not in ids

    def test_delete_unknown_id_is_ok(self, client):
        """DELETE is idempotent — deleting a missing id should not error."""
        r = client.delete("/api/templates/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/settings  – badge endpoint
# ---------------------------------------------------------------------------

class TestApiSettings:
    def test_empty_when_no_templates(self, client):
        r = client.get("/api/settings")
        assert r.status_code == 200
        assert r.get_json() == {}

    def test_returns_most_recent_template_name(self, client):
        client.post("/api/templates", content_type="application/json",
                    data=json.dumps(_payload("First")))
        client.post("/api/templates", content_type="application/json",
                    data=json.dumps(_payload("Second")))
        r = client.get("/api/settings")
        assert r.get_json()["template_name"] == "Second"


# ---------------------------------------------------------------------------
# Persistence – data survives a simulated restart (same DB file, new app)
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_templates_survive_reload(self, tmp_path, monkeypatch):
        db_file = tmp_path / "persist.db"
        monkeypatch.setenv("DATABASE_PATH", str(db_file))

        import importlib, app as app_module

        # First "boot" – create a template.
        importlib.reload(app_module)
        with app_module.app.test_client() as c:
            c.post("/api/templates", content_type="application/json",
                   data=json.dumps(_payload("Persistent Corp")))

        # Second "boot" – reload the module (simulates restart with same DB).
        importlib.reload(app_module)
        with app_module.app.test_client() as c:
            items = c.get("/api/templates").get_json()

        assert len(items) == 1
        assert items[0]["template_name"] == "Persistent Corp"


# ---------------------------------------------------------------------------
# _build_template_json – linter rule generation
# ---------------------------------------------------------------------------

class TestBuildTemplateJson:
    @pytest.fixture(autouse=True)
    def _import(self, app):
        import importlib, app as app_module
        self.build = app_module._build_template_json

    def test_default_values_produce_no_rules(self):
        doc = self.build(_payload())
        always = doc["rules"]["branding"]["typography"]["always"]
        assert always == []

    def test_non_default_font_family_adds_rule(self):
        doc = self.build(_payload(h1={
            "font_family": "Arial", "font_size": None,
            "font_color": None, "bold": None, "italic": None,
        }))
        always = doc["rules"]["branding"]["typography"]["always"]
        h1_rule = next(r for r in always if "Heading 1" in r["match"]["style_name"])
        assert h1_rule["require"]["font_name"] == "Arial"

    def test_non_default_font_size_adds_rule(self):
        doc = self.build(_payload(body={
            "font_family": None, "font_size": 14,
            "font_color": None, "bold": None, "italic": None,
        }))
        always = doc["rules"]["branding"]["typography"]["always"]
        body_rule = next(r for r in always if r["match"]["style_name"] == "Normal")
        assert body_rule["require"]["font_size"] == 14.0

    def test_bold_true_adds_rule(self):
        doc = self.build(_payload(h1={
            "font_family": None, "font_size": None,
            "font_color": None, "bold": True, "italic": None,
        }))
        always = doc["rules"]["branding"]["typography"]["always"]
        h1_rule = always[0]
        assert h1_rule["require"]["bold"] is True

    def test_none_section_handled_gracefully(self):
        """Section value of None must not raise."""
        payload = {"template_name": "T", "typography": {"h1": None, "body": None, "captions": None}}
        doc = self.build(payload)
        assert doc["rules"]["branding"]["typography"]["always"] == []

    def test_template_name_falls_back_to_default(self):
        doc = self.build({"template_name": "", "typography": {}})
        assert doc["template_name"] == "Custom Rules"

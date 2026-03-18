"""
Tests for the Flask API routes and Supabase-backed template persistence.

The Supabase client is replaced with an in-memory stub so no network calls
are made during the test suite.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# In-memory Supabase stub
# ---------------------------------------------------------------------------

class _Result:
    def __init__(self, data):
        self.data = data


class _TableQuery:
    """Minimal fluent builder that mirrors the supabase-py query API."""

    def __init__(self, rows: list):
        self._rows = rows          # reference to shared list (mutated in-place)
        self._filters: list[tuple] = []
        self._order_col = None
        self._order_desc = False
        self._limit_val = None
        self._is_maybe_single = False
        self._op = "select"
        self._upsert_row = None

    def select(self, *args, **kwargs) -> "_TableQuery":
        self._op = "select"
        return self

    def order(self, col, desc=False) -> "_TableQuery":
        self._order_col = col
        self._order_desc = desc
        return self

    def eq(self, col, val) -> "_TableQuery":
        self._filters.append((col, val))
        return self

    def limit(self, n) -> "_TableQuery":
        self._limit_val = n
        return self

    def maybe_single(self) -> "_TableQuery":
        self._is_maybe_single = True
        return self

    def upsert(self, row: dict) -> "_TableQuery":
        self._op = "upsert"
        self._upsert_row = dict(row)
        return self

    def delete(self) -> "_TableQuery":
        self._op = "delete"
        return self

    def _filtered(self) -> list:
        data = list(self._rows)
        for col, val in self._filters:
            data = [r for r in data if r.get(col) == val]
        return data

    def execute(self) -> _Result:
        if self._op == "upsert":
            row = self._upsert_row
            self._rows[:] = [r for r in self._rows if r.get("id") != row.get("id")]
            self._rows.append(row)
            return _Result([row])

        if self._op == "delete":
            ids = {r.get("id") for r in self._filtered()}
            self._rows[:] = [r for r in self._rows if r.get("id") not in ids]
            return _Result([])

        # select
        data = self._filtered()
        if self._order_col:
            data = sorted(
                data,
                key=lambda r: (r.get(self._order_col) or ""),
                reverse=self._order_desc,
            )
        if self._limit_val is not None:
            data = data[: self._limit_val]
        if self._is_maybe_single:
            return _Result(data[0] if data else None)
        return _Result(data)


class _InMemorySupabase:
    """Drop-in replacement for the supabase.Client in tests."""

    def __init__(self):
        self._tables: dict[str, list] = {}

    def table(self, name: str) -> _TableQuery:
        if name not in self._tables:
            self._tables[name] = []
        return _TableQuery(self._tables[name])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def app():
    """Return a Flask test app with an isolated in-memory Supabase stub."""
    import importlib
    import app as app_module
    importlib.reload(app_module)

    # Inject the in-memory stub before the lazy getter is ever called.
    # _get_supabase() checks `if _supabase is None`, so setting it here
    # prevents any real network call.
    app_module._supabase = _InMemorySupabase()

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
# Persistence – data survives within a session (shared Supabase store)
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_templates_survive_across_requests(self, client):
        """Data written in one request is visible in a subsequent request."""
        client.post("/api/templates", content_type="application/json",
                    data=json.dumps(_payload("Persistent Corp")))
        items = client.get("/api/templates").get_json()
        assert len(items) == 1
        assert items[0]["template_name"] == "Persistent Corp"


# ---------------------------------------------------------------------------
# _build_template_json – linter rule generation
# ---------------------------------------------------------------------------

class TestBuildTemplateJson:
    @pytest.fixture(autouse=True)
    def _import(self, app):
        import importlib
        import app as app_module
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


# ---------------------------------------------------------------------------
# Home page (GET /)
# ---------------------------------------------------------------------------

class TestHomePage:
    def test_no_templates_shows_notice(self, client):
        r = client.get("/")
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert "No templates saved yet" in html
        assert 'href="/settings"' in html
        assert 'id="template-select"' not in html

    def test_user_templates_appear_in_dropdown(self, client):
        client.post("/api/templates", content_type="application/json",
                    data=json.dumps(_payload("Acme Brand")))
        r = client.get("/")
        html = r.get_data(as_text=True)
        assert "Acme Brand" in html
        assert 'id="template-select"' in html
        assert "Newsletter" not in html

    def test_multiple_user_templates_all_listed(self, client):
        client.post("/api/templates", content_type="application/json",
                    data=json.dumps(_payload("Alpha")))
        client.post("/api/templates", content_type="application/json",
                    data=json.dumps(_payload("Beta")))
        html = client.get("/").get_data(as_text=True)
        assert "Alpha" in html
        assert "Beta" in html


# ---------------------------------------------------------------------------
# Lint with user template (integration)
# ---------------------------------------------------------------------------

class TestLintWithUserTemplate:
    """Verify the full save-then-lint flow works end-to-end."""

    def _make_minimal_docx(self, tmp_path: Path) -> Path:
        from docx import Document
        doc = Document()
        doc.add_paragraph("Hello world", style="Normal")
        p = tmp_path / "test.docx"
        doc.save(str(p))
        return p

    def test_lint_with_user_template_returns_200(self, client, tmp_path):
        r = client.post("/api/templates", content_type="application/json",
                        data=json.dumps(_payload("Corp Brand", h1={
                            "font_family": "Arial", "font_size": 14,
                            "font_color": None, "bold": True, "italic": None,
                        })))
        tid = r.get_json()["id"]

        docx_path = self._make_minimal_docx(tmp_path)
        with open(docx_path, "rb") as fh:
            res = client.post("/lint", data={
                "template": tid,
                "file": (fh, "test.docx"),
            }, content_type="multipart/form-data")

        assert res.status_code == 200
        body = res.get_json()
        assert "session_id" in body
        assert "paragraphs" in body

    def test_lint_unknown_template_returns_400(self, client, tmp_path):
        docx_path = self._make_minimal_docx(tmp_path)
        with open(docx_path, "rb") as fh:
            res = client.post("/lint", data={
                "template": "00000000-0000-0000-0000-000000000000",
                "file": (fh, "test.docx"),
            }, content_type="multipart/form-data")
        assert res.status_code == 400

    def test_bundled_template_slug_no_longer_resolves(self, client, tmp_path):
        """newsletter_external must not be accessible via the lint endpoint."""
        docx_path = self._make_minimal_docx(tmp_path)
        with open(docx_path, "rb") as fh:
            res = client.post("/lint", data={
                "template": "newsletter_external",
                "file": (fh, "test.docx"),
            }, content_type="multipart/form-data")
        assert res.status_code == 400

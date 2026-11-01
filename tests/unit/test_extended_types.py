"""Tests for the extended entity types and custom categories.

These tests run the real python-kanka managers and only replace the HTTP
layer (KankaClient._request) with a fake router, so the URLs and payloads
sent to Kanka are checked as well.
"""

import json
from typing import Any
from unittest.mock import patch

import pytest

from mcp_kanka.__main__ import list_tools
from mcp_kanka.operations import KankaOperations
from mcp_kanka.service import KankaService

TS = "2026-11-01T10:00:00.000000Z"


def entity_payload(**overrides: Any) -> dict[str, Any]:
    """Minimal payload of a type endpoint (e.g. items/5)."""
    data: dict[str, Any] = {
        "id": 5,
        "entity_id": 500,
        "name": "Dolch von Naparaja",
        "entry": "<p>Ein alter Dolch.</p>",
        "is_private": True,
        "tags": [],
        "created_at": TS,
        "created_by": 1,
        "updated_at": TS,
        "updated_by": 1,
        "type": "Waffe",
    }
    data.update(overrides)
    return data


class FakeKanka:
    """Records requests and answers them from a routing table."""

    def __init__(self, routes: dict[tuple[str, str], Any]):
        self.routes = routes
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(self, method: str, endpoint: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((method, endpoint, kwargs))
        key = (method, endpoint)
        if key not in self.routes:
            raise AssertionError(f"Unexpected request {method} {endpoint}")
        answer = self.routes[key]
        return answer(kwargs) if callable(answer) else answer

    def find(self, method: str, endpoint: str) -> list[dict[str, Any]]:
        return [kw for m, e, kw in self.calls if m == method and e == endpoint]


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> KankaService:
    monkeypatch.setenv("KANKA_TOKEN", "test-token")
    monkeypatch.setenv("KANKA_CAMPAIGN_ID", "309818")
    return KankaService()


def use(service: KankaService, routes: dict[tuple[str, str], Any]) -> FakeKanka:
    fake = FakeKanka(routes)
    patch.object(service.client, "_request", side_effect=fake).start()
    return fake


@pytest.fixture(autouse=True)
def _stop_patches():
    yield
    patch.stopall()


class TestItems:
    def test_get_item_returns_type_specific_fields(self, service):
        use(
            service,
            {
                ("GET", "entities/500"): {
                    "data": {"id": 500, "type": "item", "child": {"id": 5}}
                },
                ("GET", "items/5"): {
                    "data": entity_payload(price="unbezahlbar", size="klein")
                },
            },
        )

        result = service.get_entity_by_id(500)

        assert result is not None
        assert result["entity_type"] == "item"
        assert result["name"] == "Dolch von Naparaja"
        assert result["type"] == "Waffe"
        assert result["is_hidden"] is True
        assert result["entry"].strip() == "Ein alter Dolch."
        assert result["fields"] == {"price": "unbezahlbar", "size": "klein"}

    def test_entity_type_code_field_is_understood(self, service):
        use(
            service,
            {
                ("GET", "entities/500"): {
                    "data": {
                        "id": 500,
                        "type": "Waffe",
                        "entity_type_code": "item",
                        "child_id": 5,
                    }
                },
                ("GET", "items/5"): {"data": entity_payload()},
            },
        )

        result = service.get_entity_by_id(500)

        assert result is not None
        assert result["entity_type"] == "item"

    def test_create_item_sends_fields(self, service):
        fake = use(
            service,
            {("POST", "items"): {"data": entity_payload(price="10 Francs")}},
        )

        result = service.create_entity(
            entity_type="item",
            name="Dolch von Naparaja",
            type="Waffe",
            entry="Ein **alter** Dolch.",
            is_hidden=True,
            fields={"price": "10 Francs", "location_id": 7, "name": "ignored"},
        )

        body = fake.find("POST", "items")[0]["json"]
        assert body["name"] == "Dolch von Naparaja"
        assert body["price"] == "10 Francs"
        assert body["location_id"] == 7
        assert body["is_private"] is True
        assert "<strong>alter</strong>" in body["entry"]
        assert result["mention"] == "[entity:500]"
        assert result["entity_type"] == "item"

    def test_update_item_sends_fields(self, service):
        fake = use(
            service,
            {
                ("GET", "entities/500"): {
                    "data": {"id": 500, "type": "item", "child": {"id": 5}}
                },
                ("GET", "items/5"): {"data": entity_payload()},
                ("PUT", "items/5"): {"data": entity_payload(size="groß")},
                ("PATCH", "items/5"): {"data": entity_payload(size="groß")},
            },
        )

        assert service.update_entity(500, name="Dolch", fields={"size": "groß"})

        writes = fake.find("PUT", "items/5") + fake.find("PATCH", "items/5")
        assert len(writes) == 1
        assert writes[0]["json"]["size"] == "groß"
        assert writes[0]["json"]["name"] == "Dolch"

    def test_delete_item_is_refused(self, service):
        fake = use(
            service,
            {
                ("GET", "entities/500"): {
                    "data": {"id": 500, "type": "item", "child": {"id": 5}}
                },
                ("GET", "items/5"): {"data": entity_payload()},
            },
        )

        with pytest.raises(ValueError, match="disabled"):
            service.delete_entity(500)

        assert not [c for c in fake.calls if c[0] == "DELETE"]

    def test_post_on_item(self, service):
        fake = use(
            service,
            {
                ("GET", "entities/500"): {
                    "data": {"id": 500, "type": "item", "child": {"id": 5}}
                },
                ("GET", "items/5"): {"data": entity_payload()},
                ("POST", "entities/500/posts"): {
                    "data": {
                        "id": 77,
                        "name": "Notiz",
                        "entry": "<p>x</p>",
                        "entity_id": 500,
                        "created_by": 1,
                        "created_at": TS,
                        "updated_at": TS,
                        "visibility_id": 2,
                    }
                },
            },
        )

        result = service.create_post(500, name="Notiz", entry="x", is_hidden=True)

        assert result == {"post_id": 77, "entity_id": 500}
        body = fake.find("POST", "entities/500/posts")[0]["json"]
        assert body["visibility_id"] == 2


class TestOtherModules:
    @pytest.mark.parametrize(
        "code,endpoint,our_type",
        [
            ("event", "events", "event"),
            ("family", "families", "family"),
            ("calendar", "calendars", "calendar"),
            ("ability", "abilities", "ability"),
            ("timeline", "timelines", "timeline"),
            ("map", "maps", "map"),
        ],
    )
    def test_get_entity_for_each_module(self, service, code, endpoint, our_type):
        use(
            service,
            {
                ("GET", "entities/500"): {
                    "data": {"id": 500, "type": code, "child": {"id": 5}}
                },
                ("GET", f"{endpoint}/5"): {"data": entity_payload()},
            },
        )

        result = service.get_entity_by_id(500)

        assert result is not None
        assert result["entity_type"] == our_type


class TestCustomCategories:
    def test_list_categories(self, service):
        use(
            service,
            {
                ("GET", "entity_types"): {
                    "data": [
                        {
                            "id": 1,
                            "code": "character",
                            "singular": "Charakter",
                            "plural": "Charaktere",
                            "is_custom": False,
                        },
                        {
                            "id": 42,
                            "code": "sessionplanung",
                            "singular": "Sessionplanung",
                            "plural": "Sessionplanungen",
                            "is_custom": True,
                        },
                    ]
                }
            },
        )

        categories = service.list_categories()

        assert categories[1] == {
            "category_id": 42,
            "code": "sessionplanung",
            "singular": "Sessionplanung",
            "plural": "Sessionplanungen",
            "is_custom": True,
        }

    def test_list_category_entities_filters_by_type_id(self, service):
        fake = use(
            service,
            {
                ("GET", "entities"): {
                    "data": [{"id": 900, "name": "Sessionplanung 3.04", "type_id": 42}],
                    "links": {"next": None},
                }
            },
        )

        entries = service.list_category_entities(42)

        assert fake.find("GET", "entities")[0]["params"]["type_id[]"] == 42
        assert entries[0]["entity_id"] == 900
        assert entries[0]["entity_type"] == "custom"
        assert entries[0]["category_id"] == 42

    def test_get_custom_entity_with_entry_and_posts(self, service):
        def entity_response(kwargs: dict[str, Any]) -> dict[str, Any]:
            base = {
                "id": 900,
                "name": "Sessionplanung 3.04",
                "type": "Planung",
                "entity_type_code": "sessionplanung",
                "type_id": 42,
                "is_private": True,
            }
            if kwargs.get("params", {}).get("related"):
                base["entry"] = "<p>Szene 1: Der Mob</p>"
            return {"data": base}

        use(
            service,
            {
                ("GET", "entities/900"): entity_response,
                ("GET", "entities/900/posts"): {
                    "data": [
                        {
                            "id": 1,
                            "name": "Visionen",
                            "entry": "<p>Ein Leuchtturm</p>",
                            "entity_id": 900,
                            "created_by": 1,
                            "created_at": TS,
                            "updated_at": TS,
                            "visibility_id": 2,
                        }
                    ]
                },
            },
        )

        result = service.get_entity_by_id(900, include_posts=True)

        assert result is not None
        assert result["entity_type"] == "custom"
        assert result["category_id"] == 42
        assert result["type"] == "Planung"
        assert result["is_hidden"] is True
        assert result["entry"].strip() == "Szene 1: Der Mob"
        assert result["posts"][0]["name"] == "Visionen"
        assert result["posts"][0]["is_hidden"] is True

    def test_create_custom_entity(self, service):
        fake = use(
            service,
            {
                ("POST", "entities"): {
                    "data": [{"id": 901, "name": "Sessionplanung 3.05"}]
                },
                ("PATCH", "entities/901"): {"data": {"id": 901}},
            },
        )

        result = service.create_entity(
            entity_type="custom",
            category_id=42,
            name="Sessionplanung 3.05",
            entry="Der Keller",
            is_hidden=True,
        )

        body = fake.find("POST", "entities")[0]["json"]
        assert body["entities"][0]["module"] == 42
        assert body["entities"][0]["name"] == "Sessionplanung 3.05"
        assert "Der Keller" in body["entities"][0]["entry"]
        patch_body = fake.find("PATCH", "entities/901")[0]["json"]
        assert patch_body["is_private"] is True
        assert result["entity_id"] == 901
        assert result["mention"] == "[entity:901]"

    def test_create_custom_entity_requires_category(self, service):
        with pytest.raises(ValueError, match="category_id"):
            service.create_entity(entity_type="custom", name="X")

    def test_update_custom_entity(self, service):
        fake = use(
            service,
            {
                ("GET", "entities/900"): {
                    "data": {
                        "id": 900,
                        "name": "Sessionplanung 3.04",
                        "entity_type_code": "sessionplanung",
                        "type_id": 42,
                        "entry": "<p>alt</p>",
                    }
                },
                ("PATCH", "entities/900"): {"data": {"id": 900}},
            },
        )

        assert service.update_entity(900, name="Sessionplanung 3.04", entry="neu")

        body = fake.find("PATCH", "entities/900")[0]["json"]
        assert body["name"] == "Sessionplanung 3.04"
        assert "neu" in body["entry"]


class TestOperationsAndTools:
    @pytest.mark.asyncio
    async def test_find_custom_requires_category(self, service):
        ops = KankaOperations(service=service)
        result = await ops.find_entities(entity_type="custom")
        assert result["entities"] == []

    @pytest.mark.asyncio
    async def test_find_custom_entities(self, service):
        use(
            service,
            {
                ("GET", "entities"): {
                    "data": [
                        {"id": 900, "name": "Sessionplanung 3.04", "type_id": 42},
                        {"id": 901, "name": "Sessionplanung 3.05", "type_id": 42},
                    ],
                    "links": {"next": None},
                }
            },
        )
        ops = KankaOperations(service=service)

        result = await ops.find_entities(
            entity_type="custom", category_id=42, name="3.05", include_full=False
        )

        assert result["entities"] == [
            {"entity_id": 901, "name": "Sessionplanung 3.05", "entity_type": "custom"}
        ]

    @pytest.mark.asyncio
    async def test_get_entities_passes_fields(self, service):
        use(
            service,
            {
                ("GET", "entities/500"): {
                    "data": {"id": 500, "type": "item", "child": {"id": 5}}
                },
                ("GET", "items/5"): {"data": entity_payload(price="1 Franc")},
            },
        )
        ops = KankaOperations(service=service)

        result = await ops.get_entities([500])

        assert result[0]["success"] is True
        assert result[0]["entity_type"] == "item"
        assert result[0]["fields"] == {"price": "1 Franc"}

    @pytest.mark.asyncio
    async def test_tool_schemas(self):
        tools = {tool.name: tool for tool in await list_tools()}

        assert "list_categories" in tools
        find_enum = tools["find_entities"].inputSchema["properties"]["entity_type"][
            "enum"
        ]
        assert {"item", "event", "ability", "custom"} <= set(find_enum)
        create_props = tools["create_entities"].inputSchema["properties"]["entities"][
            "items"
        ]["properties"]
        assert "fields" in create_props
        assert "category_id" in create_props
        json.dumps([tool.inputSchema for tool in tools.values()])

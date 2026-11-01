"""Service layer for Kanka API operations."""

# mypy: warn_return_any=False

import logging
import os
from typing import Any

from kanka import KankaClient
from kanka.exceptions import KankaException
from kanka.managers import EntityManager
from kanka.models import (
    Calendar,
    Character,
    Creature,
    Entity,
    Event,
    Family,
    Journal,
    Location,
    Note,
    Organisation,
    Quest,
    Race,
    Tag,
)

from .converter import ContentConverter
from .types import (
    DELETABLE_ENTITY_TYPES,
    MODULE_ENTITY_TYPES,
    EntityType,
)

logger = logging.getLogger(__name__)


class Item(Entity):
    """Item (object) entity. Not modelled by python-kanka, so defined here."""

    type: str | None = None


class Ability(Entity):
    """Ability entity. Not modelled by python-kanka, so defined here."""

    type: str | None = None


class Timeline(Entity):
    """Timeline entity. Not modelled by python-kanka, so defined here."""

    type: str | None = None


class Map(Entity):
    """Map entity. Not modelled by python-kanka, so defined here."""

    type: str | None = None


# Fields of the base Entity model and fields that the service already exposes
# under their own keys. Everything else on a model is returned under "fields".
_BASE_FIELD_NAMES = set(Entity.model_fields) | {
    "type",
    "is_completed",
    "image_uuid",
    "header_uuid",
    "has_custom_image",
    "has_custom_header",
    "image_path",
    "header_image",
    "entity_type",
    "entry_parsed",
    "tooltip",
    "focus_x",
    "focus_y",
    "is_template",
    "is_attributes_private",
    "campaign_id",
    "urls",
    "child",
    "child_id",
    "type_id",
}

# Keys the generic "fields" parameter may not override, because they are set
# through dedicated parameters.
_PROTECTED_FIELD_KEYS = {
    "id",
    "entity_id",
    "name",
    "entry",
    "is_private",
    "tags",
    "type",
    "is_completed",
    "image_uuid",
    "header_uuid",
}


class KankaService:
    """Service layer wrapping the python-kanka client."""

    # Map entity types to their model classes
    ENTITY_TYPE_MAP: dict[str, type[Entity]] = {
        "character": Character,
        "creature": Creature,
        "location": Location,
        "organization": Organisation,  # Note: Kanka uses "organisation"
        "race": Race,
        "note": Note,
        "journal": Journal,
        "quest": Quest,
        "event": Event,
        "family": Family,
        "calendar": Calendar,
        "item": Item,
        "ability": Ability,
        "timeline": Timeline,
        "map": Map,
    }

    # Map entity types to their Kanka API endpoints
    API_ENDPOINT_MAP = {
        "character": "characters",
        "creature": "creatures",
        "location": "locations",
        "organization": "organisations",  # API uses British spelling
        "race": "races",
        "note": "notes",
        "journal": "journals",
        "quest": "quests",
        "event": "events",
        "family": "families",
        "calendar": "calendars",
        "item": "items",
        "ability": "abilities",
        "timeline": "timelines",
        "map": "maps",
    }

    # Map Kanka's entity type codes to our internal types
    API_CODE_MAP = {
        "character": "character",
        "creature": "creature",
        "location": "location",
        "organisation": "organization",
        "organization": "organization",
        "race": "race",
        "note": "note",
        "journal": "journal",
        "quest": "quest",
        "event": "event",
        "family": "family",
        "calendar": "calendar",
        "item": "item",
        "object": "item",
        "ability": "ability",
        "timeline": "timeline",
        "map": "map",
    }

    def __init__(self) -> None:
        """Initialize the service with Kanka client."""
        token = os.getenv("KANKA_TOKEN")
        campaign_id = os.getenv("KANKA_CAMPAIGN_ID")

        if not token or not campaign_id:
            raise ValueError(
                "KANKA_TOKEN and KANKA_CAMPAIGN_ID environment variables are required"
            )

        self.client = KankaClient(token=token, campaign_id=int(campaign_id))
        self.converter = ContentConverter()
        self._tag_cache: dict[str, Tag] = {}
        self._managers: dict[str, Any] = {}
        # Posts live under entities/{entity_id}/posts for every entity type,
        # so one generic manager serves entities without a type manager
        # (e.g. entries of custom categories).
        self._post_manager: Any = EntityManager(self.client, "entities", Entity)

    def _manager(self, entity_type: str) -> Any:
        """
        Get the manager for an entity type.

        python-kanka ships managers for some types; for the others
        (items, abilities, timelines, maps) a generic EntityManager is built.
        """
        if entity_type not in self.API_ENDPOINT_MAP:
            raise ValueError(f"Unsupported entity type: {entity_type}")

        if entity_type not in self._managers:
            endpoint = self.API_ENDPOINT_MAP[entity_type]
            manager = getattr(self.client, endpoint, None)
            if manager is None:
                manager = EntityManager(
                    self.client, endpoint, self.ENTITY_TYPE_MAP[entity_type]
                )
            self._managers[entity_type] = manager
        return self._managers[entity_type]

    def _post_manager_for(self, entity_type: str) -> Any:
        """Get a manager that can handle posts for an entity type."""
        if entity_type in self.API_ENDPOINT_MAP:
            return self._manager(entity_type)
        return self._post_manager

    def search_entities(
        self,
        query: str,
        entity_type: EntityType | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Search for entities by name using list endpoints with filtering.

        This uses the list endpoints with name filtering instead of the search API,
        as they provide the same partial matching capability but with more control.

        Args:
            query: Search query (matches partial names)
            entity_type: Optional entity type filter
            limit: Maximum results

        Returns:
            List of minimal entity data
        """
        try:
            entities = []

            if entity_type:
                # Search specific entity type
                manager = self._manager(entity_type)

                # Use name filter to search - it does partial matching!
                results = manager.list(name=query, limit=limit)

                for entity in results:
                    entities.append(
                        {
                            "entity_id": entity.entity_id,
                            "name": entity.name,
                            "entity_type": entity_type,
                        }
                    )
            else:
                # Search across all entity types
                # We'll need to query each type separately
                remaining_limit = limit

                for our_type in MODULE_ENTITY_TYPES:
                    if remaining_limit <= 0:
                        break

                    manager = self._manager(our_type)

                    # Get up to remaining_limit results from this type
                    type_limit = min(remaining_limit, 100)  # API max is 100

                    try:
                        results = manager.list(name=query, limit=type_limit)

                        for entity in results:
                            entities.append(
                                {
                                    "entity_id": entity.entity_id,
                                    "name": entity.name,
                                    "entity_type": our_type,
                                }
                            )

                        remaining_limit -= len(results)

                    except Exception as e:
                        # Some entity types might not be available in the campaign
                        logger.debug(f"Could not search {our_type}: {e}")
                        continue

            return entities

        except KankaException as e:
            logger.error(f"Search failed: {e}")
            raise

    def list_entities(
        self,
        entity_type: EntityType,
        page: int = 1,
        limit: int = 100,
        last_sync: str | None = None,
        related: bool = False,
    ) -> list[Entity]:
        """
        List entities of a specific type.

        Args:
            entity_type: Entity type to list
            page: Page number
            limit: Results per page (0 for all)
            last_sync: ISO 8601 timestamp to get only entities modified after this time
            related: Include related data (posts, attributes, etc.)

        Returns:
            List of entity objects
        """
        try:
            manager = self._manager(entity_type)

            # Build filters
            filters = {}
            if last_sync:
                filters["lastSync"] = last_sync

            if limit == 0:
                # Get all results by paginating through all API pages
                # Use the proper pagination info from the SDK
                all_entities = []
                current_page = 1
                logger.debug(
                    f"Starting pagination for {entity_type} with related={related}"
                )

                while True:
                    logger.debug(f"Fetching page {current_page}")
                    try:
                        batch = manager.list(
                            page=current_page, related=related, **filters
                        )
                        logger.debug(
                            f"Page {current_page} returned {len(batch)} entities"
                        )

                        # Add current page results
                        all_entities.extend(batch)

                        # Check if there's a next page using SDK pagination info
                        if not manager.has_next_page:
                            logger.debug("No more pages, stopping pagination")
                            break

                        current_page += 1

                        # Safety limit to prevent infinite loops
                        if current_page > 50:
                            logger.warning(
                                f"Hit safety limit of 50 pages for {entity_type}"
                            )
                            break

                    except Exception as e:
                        logger.error(
                            f"Error fetching page {current_page} for {entity_type}: {e}"
                        )
                        break

                logger.debug(
                    f"Pagination complete for {entity_type}: {len(all_entities)} total entities"
                )
                entities = all_entities
            else:
                # Get limited results (client-side limiting)
                # Fetch pages until we have enough entities
                all_entities = []
                current_page = page  # Start from requested page
                logger.debug(
                    f"Fetching for client-side limit of {limit} {entity_type}s starting from page {page}"
                )

                while len(all_entities) < limit:
                    try:
                        batch = manager.list(
                            page=current_page, related=related, **filters
                        )

                        all_entities.extend(batch)

                        # Stop if no more pages or we have enough
                        if not manager.has_next_page or len(all_entities) >= limit:
                            break

                        current_page += 1

                        # Safety limit
                        if current_page > 50:
                            logger.warning(
                                f"Hit safety limit of 50 pages for {entity_type}"
                            )
                            break

                    except Exception as e:
                        logger.error(
                            f"Error fetching page {current_page} for {entity_type}: {e}"
                        )
                        break

                # Apply client-side limit
                entities = all_entities[:limit]

            return list(entities)

        except KankaException as e:
            logger.error(f"List entities failed: {e}")
            raise

    def _resolve_entity_type(self, found_entity: dict[str, Any]) -> str | None:
        """
        Map the data of the /entities/{id} endpoint to our internal type.

        Kanka has used different field names for the type code over time, so
        several candidates are checked. Returns None for custom categories.
        """
        for key in ("type", "entity_type", "entity_type_code"):
            code = found_entity.get(key)
            if isinstance(code, str) and code.lower() in self.API_CODE_MAP:
                return self.API_CODE_MAP[code.lower()]
        return None

    def list_categories(self) -> list[dict[str, Any]]:
        """
        List the campaign's categories (built-in and custom).

        Returns:
            List of categories with id, code, singular, plural and is_custom
        """
        response = self.client._request("GET", "entity_types")
        categories = []
        for item in response.get("data", []):
            categories.append(
                {
                    "category_id": item.get("id"),
                    "code": item.get("code"),
                    "singular": item.get("singular"),
                    "plural": item.get("plural"),
                    "is_custom": bool(item.get("is_custom")),
                }
            )
        return categories

    def list_category_entities(
        self, category_id: int, last_sync: str | None = None
    ) -> list[dict[str, Any]]:
        """
        List all entries of a category (works for custom categories).

        Args:
            category_id: The category id (from list_categories)
            last_sync: ISO 8601 timestamp to get only entries modified after it

        Returns:
            List of entity dictionaries (entry not included by the API)
        """
        results: list[dict[str, Any]] = []
        page = 1
        while page <= 50:
            params: dict[str, Any] = {
                "type_id[]": category_id,
                "page": page,
                "limit": 100,
            }
            if last_sync:
                params["lastSync"] = last_sync
            response = self.client._request("GET", "entities", params=params)
            for item in response.get("data", []):
                results.append(self._custom_entity_to_dict(item))
            if not response.get("links", {}).get("next"):
                break
            page += 1
        return results

    def _custom_entity_to_dict(
        self, data: dict[str, Any], category_id: int | None = None
    ) -> dict[str, Any]:
        """Convert raw /entities data of a custom category entry to our format."""
        child = data.get("child") if isinstance(data.get("child"), dict) else {}
        entry_html = data.get("entry") or (child or {}).get("entry")
        is_private = data.get("is_private")

        result: dict[str, Any] = {
            "id": data.get("id"),
            "entity_id": data.get("id"),
            "name": data.get("name"),
            "entity_type": "custom",
            "category_id": category_id or data.get("type_id"),
            "type": data.get("type") if isinstance(data.get("type"), str) else None,
            "entry": (
                self.converter.html_to_markdown(entry_html) if entry_html else None
            ),
            "tags": self._resolve_tag_names(data.get("tags") or []),
            "is_hidden": bool(is_private) if is_private is not None else False,
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
            "image": None,
            "image_full": None,
            "image_thumb": None,
            "image_uuid": data.get("image_uuid"),
            "header_uuid": data.get("header_uuid"),
        }
        parent_id = data.get("parent_id")
        if parent_id is not None:
            result["fields"] = {"parent_id": parent_id}
        return result

    def _get_custom_entity(
        self,
        entity_id: int,
        found_entity: dict[str, Any],
        include_posts: bool,
    ) -> dict[str, Any] | None:
        """Get an entry of a custom category, including its entry text."""
        data = found_entity
        if not (data.get("entry") or (data.get("child") or {}).get("entry")):
            # The plain endpoint may omit the entry; ask for related data
            try:
                response = self.client._request(
                    "GET", f"entities/{entity_id}", params={"related": 1}
                )
                data = {**found_entity, **response.get("data", {})}
            except Exception as e:
                logger.debug(f"Related fetch failed for {entity_id}: {e}")

        result = self._custom_entity_to_dict(data)

        if include_posts:
            try:
                posts = self._post_manager.list_posts(entity_id, limit=100)
                result["posts"] = [self._post_to_dict(post) for post in posts]
            except Exception as e:
                logger.warning(f"Failed to get posts for entity {entity_id}: {e}")
                result["posts"] = []

        return result

    def create_custom_entity(
        self,
        category_id: int,
        name: str,
        type: str | None = None,
        entry: str | None = None,
        tags: list[str] | None = None,
        is_hidden: bool | None = None,
    ) -> dict[str, Any]:
        """
        Create an entry in a (custom) category via the generic entities endpoint.

        Args:
            category_id: Category id (from list_categories)
            name: Entry name
            type: Type field
            entry: Description in Markdown
            tags: Tag names
            is_hidden: Whether the entry should be hidden from players

        Returns:
            Created entity data
        """
        item: dict[str, Any] = {"name": name, "module": category_id}
        if type is not None:
            item["type"] = type
        if entry is not None:
            item["entry"] = self.converter.markdown_to_html(entry)
        if tags:
            item["tags"] = self._get_or_create_tag_ids(tags)

        response = self.client._request("POST", "entities", json={"entities": [item]})
        data = response.get("data")
        if isinstance(data, list):
            data = data[0] if data else {}
        if not isinstance(data, dict) or not data.get("id"):
            raise ValueError(f"Unexpected response when creating entity: {response}")

        entity_id = data["id"]
        if is_hidden is not None:
            # Privacy is not accepted on create, so set it in a second step
            self.update_custom_entity(entity_id, name=name, is_hidden=is_hidden)
            data["is_private"] = is_hidden

        result = self._custom_entity_to_dict(data, category_id)
        result["mention"] = f"[entity:{entity_id}]"
        return result

    def update_custom_entity(
        self,
        entity_id: int,
        name: str,
        type: str | None = None,
        entry: str | None = None,
        tags: list[str] | None = None,
        is_hidden: bool | None = None,
        fields: dict[str, Any] | None = None,
    ) -> bool:
        """Update an entry of a custom category via the generic entities endpoint."""
        data: dict[str, Any] = {"name": name}
        if type is not None:
            data["type"] = type
        if entry is not None:
            data["entry"] = self.converter.markdown_to_html(entry)
        if is_hidden is not None:
            data["is_private"] = is_hidden
        if tags is not None:
            data["tags"] = self._get_or_create_tag_ids(tags)
        data.update(self._clean_fields(fields))

        self.client._request("PATCH", f"entities/{entity_id}", json=data)
        return True

    @staticmethod
    def _clean_fields(fields: dict[str, Any] | None) -> dict[str, Any]:
        """Drop keys from generic fields that have dedicated parameters."""
        if not fields:
            return {}
        return {k: v for k, v in fields.items() if k not in _PROTECTED_FIELD_KEYS}

    def get_entity_by_id(
        self, entity_id: int, include_posts: bool = False
    ) -> dict[str, Any] | None:
        """
        Get a specific entity by its entity_id.

        Args:
            entity_id: Entity ID
            include_posts: Whether to include posts

        Returns:
            Entity data with converted content
        """
        try:
            # Use the direct entity endpoint
            found_entity = self.client.entity(entity_id)

            if not found_entity:
                # Entity not found
                return None

            our_type = self._resolve_entity_type(found_entity)

            if our_type is None:
                # Not a built-in module: treat it as an entry of a custom
                # category (e.g. a campaign-specific "Session planning" type)
                return self._get_custom_entity(entity_id, found_entity, include_posts)

            # The entity endpoint returns the data in 'child' field
            child_data = found_entity.get("child") or {}

            # Get the type-specific ID
            type_id = child_data.get("id") or found_entity.get("child_id")
            if not type_id:
                return None

            # Now use the type-specific manager to get a proper entity object
            # This gives us consistent data format with datetime objects
            manager = self._manager(our_type)
            entity = manager.get(type_id)

            # Use _entity_to_dict to handle all conversions consistently
            result = self._entity_to_dict(entity, our_type)

            # Get posts if requested
            if include_posts:
                try:
                    # Get the manager for this entity type
                    manager = self._manager(our_type)
                    # Use entity_id, not the type-specific id
                    posts = manager.list_posts(entity_id, limit=100)
                    result["posts"] = [self._post_to_dict(post) for post in posts]
                except Exception as e:
                    logger.warning(f"Failed to get posts for entity {entity_id}: {e}")
                    result["posts"] = []

            return result

        except Exception as e:
            logger.error(f"Get entity failed for {entity_id}: {e}")
            return None

    def create_entity(
        self,
        entity_type: EntityType,
        name: str,
        type: str | None = None,
        entry: str | None = None,
        tags: list[str] | None = None,
        is_hidden: bool | None = None,
        is_completed: bool | None = None,
        image_uuid: str | None = None,
        header_uuid: str | None = None,
        fields: dict[str, Any] | None = None,
        category_id: int | None = None,
    ) -> dict[str, Any]:
        """
        Create a new entity.

        Args:
            entity_type: Type of entity
            name: Entity name
            type: Entity subtype
            entry: Description in Markdown
            tags: List of tag names
            is_hidden: Whether entity should be hidden from players (admin-only)
            is_completed: Whether quest is completed (quests only)
            image_uuid: Image gallery UUID for entity image
            header_uuid: Image gallery UUID for entity header
            fields: Additional type-specific API fields (e.g. price for items)
            category_id: Category id, required for entity_type "custom"

        Returns:
            Created entity data
        """
        try:
            if entity_type == "custom":
                if not category_id:
                    raise ValueError("category_id is required for entity_type 'custom'")
                return self.create_custom_entity(
                    category_id=category_id,
                    name=name,
                    type=type,
                    entry=entry,
                    tags=tags,
                    is_hidden=is_hidden,
                )

            manager = self._manager(entity_type)

            # Prepare data
            data: dict[str, Any] = {"name": name}

            if type is not None:
                data["type"] = type

            if entry is not None:
                # Convert markdown to HTML
                data["entry"] = self.converter.markdown_to_html(entry)

            # Set privacy based on is_hidden
            # For entities, use is_private (not visibility_id)
            if is_hidden is not None:
                data["is_private"] = is_hidden
            elif entity_type == "note":
                # Notes default to private
                data["is_private"] = True
            else:
                # Default to public
                data["is_private"] = False

            # Handle tags
            if tags:
                tag_ids = self._get_or_create_tag_ids(tags)
                data["tags"] = tag_ids

            # Handle quest-specific field
            if entity_type == "quest" and is_completed is not None:
                data["is_completed"] = is_completed

            # Handle image fields
            if image_uuid is not None:
                data["image_uuid"] = image_uuid
            if header_uuid is not None:
                data["header_uuid"] = header_uuid

            # Type-specific fields (price, location_id, date, ...)
            data.update(self._clean_fields(fields))

            # Create entity
            entity = manager.create(**data)

            # Convert to our format
            result = self._entity_to_dict(entity, entity_type)
            result["mention"] = f"[entity:{entity.entity_id}]"

            # If we explicitly set privacy, ensure it's reflected in the result
            # The API might not return is_private in the create response
            if "is_private" in data:
                result["is_hidden"] = data["is_private"]

            return result

        except KankaException as e:
            logger.error(f"Create entity failed: {e}")
            raise

    def update_entity(
        self,
        entity_id: int,
        name: str,
        type: str | None = None,
        entry: str | None = None,
        tags: list[str] | None = None,
        is_hidden: bool | None = None,
        is_completed: bool | None = None,
        image_uuid: str | None = None,
        header_uuid: str | None = None,
        fields: dict[str, Any] | None = None,
    ) -> bool:
        """
        Update an existing entity.

        Args:
            entity_id: Entity ID
            name: Entity name (required by API)
            type: Entity subtype
            entry: Description in Markdown
            tags: List of tag names
            is_hidden: Whether entity should be hidden from players (admin-only)
            is_completed: Whether quest is completed (quests only)
            image_uuid: Image gallery UUID for entity image
            header_uuid: Image gallery UUID for entity header
            fields: Additional type-specific API fields (e.g. price for items)

        Returns:
            True if successful
        """
        try:
            # First get the entity to find its type
            entity_data = self.get_entity_by_id(entity_id)
            if not entity_data:
                raise ValueError(f"Entity {entity_id} not found")

            entity_type = entity_data["entity_type"]
            if entity_type == "custom":
                return self.update_custom_entity(
                    entity_id,
                    name=name,
                    type=type,
                    entry=entry,
                    tags=tags,
                    is_hidden=is_hidden,
                    fields=fields,
                )

            manager = self._manager(entity_type)

            # Prepare update data
            data: dict[str, Any] = {"name": name}

            if type is not None:
                data["type"] = type

            if entry is not None:
                # Convert markdown to HTML
                data["entry"] = self.converter.markdown_to_html(entry)

            # Handle privacy
            # For entities, use is_private (not visibility_id)
            if is_hidden is not None:
                data["is_private"] = is_hidden

            # Handle tags
            if tags is not None:
                tag_ids = self._get_or_create_tag_ids(tags)
                data["tags"] = tag_ids

            # Handle quest-specific field
            if entity_type == "quest" and is_completed is not None:
                data["is_completed"] = is_completed

            # Handle image fields
            if image_uuid is not None:
                data["image_uuid"] = image_uuid
            if header_uuid is not None:
                data["header_uuid"] = header_uuid

            # Type-specific fields (price, location_id, date, ...)
            data.update(self._clean_fields(fields))

            # Update entity
            manager.update(entity_data["id"], **data)
            return True

        except Exception as e:
            logger.error(f"Update entity failed for {entity_id}: {e}")
            raise

    def delete_entity(self, entity_id: int) -> bool:
        """
        Delete an entity.

        Args:
            entity_id: Entity ID

        Returns:
            True if successful
        """
        try:
            # First get the entity to find its type
            entity_data = self.get_entity_by_id(entity_id)
            if not entity_data:
                raise ValueError(f"Entity {entity_id} not found")

            entity_type = entity_data["entity_type"]
            if entity_type not in DELETABLE_ENTITY_TYPES:
                raise ValueError(
                    f"Deleting entities of type '{entity_type}' is disabled "
                    "in this server; delete it in Kanka directly"
                )
            manager = self._manager(entity_type)

            # Delete entity
            manager.delete(entity_data["id"])
            return True

        except Exception as e:
            logger.error(f"Delete entity failed for {entity_id}: {e}")
            raise

    def create_post(
        self,
        entity_id: int,
        name: str,
        entry: str | None = None,
        is_hidden: bool = False,
    ) -> dict[str, Any]:
        """
        Create a post on an entity.

        Args:
            entity_id: Entity ID
            name: Post title
            entry: Post content in Markdown
            is_hidden: Whether post should be hidden from players (admin-only)

        Returns:
            Created post data
        """
        try:
            # Get entity to find its type
            entity_data = self.get_entity_by_id(entity_id)
            if not entity_data:
                raise ValueError(f"Entity {entity_id} not found")

            entity_type = entity_data["entity_type"]
            manager = self._post_manager_for(entity_type)

            # Convert markdown to HTML if entry provided
            html_entry = self.converter.markdown_to_html(entry) if entry else None

            # Set visibility based on is_hidden
            visibility_id = 2 if is_hidden else 1

            # Create post - use entity_id, not the type-specific id
            post = manager.create_post(
                entity_id,
                name=name,
                entry=html_entry or "",
                visibility_id=visibility_id,
            )

            return {
                "post_id": post.id,
                "entity_id": entity_id,
            }

        except Exception as e:
            logger.error(f"Create post failed: {e}")
            raise

    def update_post(
        self,
        entity_id: int,
        post_id: int,
        name: str,
        entry: str | None = None,
        is_hidden: bool | None = None,
    ) -> bool:
        """
        Update a post.

        Args:
            entity_id: Entity ID
            post_id: Post ID
            name: Post title (required by API)
            entry: Post content in Markdown
            is_hidden: Whether post should be hidden from players (admin-only)

        Returns:
            True if successful
        """
        try:
            # Get entity to find its type
            entity_data = self.get_entity_by_id(entity_id)
            if not entity_data:
                raise ValueError(f"Entity {entity_id} not found")

            entity_type = entity_data["entity_type"]
            manager = self._post_manager_for(entity_type)

            # Prepare update data
            kwargs: dict[str, Any] = {"name": name}

            if entry is not None:
                kwargs["entry"] = self.converter.markdown_to_html(entry)

            # Handle visibility
            # For posts, use visibility_id
            visibility_id = None
            if is_hidden is not None:
                visibility_id = 2 if is_hidden else 1

            # Update post - use entity_id, not the type-specific id
            manager.update_post(
                entity_id, post_id, visibility_id=visibility_id, **kwargs
            )
            return True

        except Exception as e:
            logger.error(f"Update post failed: {e}")
            raise

    def delete_post(self, entity_id: int, post_id: int) -> bool:
        """
        Delete a post.

        Args:
            entity_id: Entity ID
            post_id: Post ID

        Returns:
            True if successful
        """
        try:
            # Get entity to find its type
            entity_data = self.get_entity_by_id(entity_id)
            if not entity_data:
                raise ValueError(f"Entity {entity_id} not found")

            entity_type = entity_data["entity_type"]
            manager = self._post_manager_for(entity_type)

            # Delete post - use entity_id, not the type-specific id
            manager.delete_post(entity_id, post_id)
            return True

        except Exception as e:
            logger.error(f"Delete post failed: {e}")
            raise

    def _get_or_create_tag_ids(self, tag_names: list[str]) -> list[int]:
        """
        Get or create tags by name.

        Args:
            tag_names: List of tag names

        Returns:
            List of tag IDs
        """
        # Load tag cache if needed
        if not self._tag_cache:
            self._load_tag_cache()

        tag_ids = []
        for name in tag_names:
            name_lower = name.lower()

            # Check cache
            if name_lower in self._tag_cache:
                tag_ids.append(self._tag_cache[name_lower].id)
            else:
                # Create new tag
                try:
                    tag = self.client.tags.create(name=name)
                    self._tag_cache[name_lower] = tag
                    tag_ids.append(tag.id)
                except Exception as e:
                    logger.warning(f"Failed to create tag '{name}': {e}")

        return tag_ids

    def _load_tag_cache(self) -> None:
        """Load all tags into cache."""
        self._tag_cache = {}
        try:
            # Get all tags by paginating through them
            current_page = 1
            while True:
                batch = self.client.tags.list(page=current_page, limit=100)
                if not batch:
                    break
                for tag in batch:
                    self._tag_cache[tag.name.lower()] = tag
                if len(batch) < 100:
                    break
                current_page += 1
        except Exception as e:
            logger.warning(f"Failed to load tag cache: {e}")

    def _resolve_tag_names(self, raw_tags: list[Any]) -> list[str]:
        """
        Resolve tag IDs to tag names.

        Args:
            raw_tags: List of tag IDs or tag objects

        Returns:
            List of tag names
        """
        if not raw_tags or not isinstance(raw_tags, list):
            return []

        # Ensure tag cache is loaded
        if not self._tag_cache:
            self._load_tag_cache()

        tag_names = []
        for tag_item in raw_tags:
            if isinstance(tag_item, int | str):
                # It's a tag ID, need to look it up
                tag_id = int(tag_item) if isinstance(tag_item, str) else tag_item

                # Check cache first
                tag_name = None
                for _cached_name, cached_tag in self._tag_cache.items():
                    if cached_tag.id == tag_id:
                        tag_name = cached_tag.name
                        break

                if tag_name:
                    tag_names.append(tag_name)
                else:
                    # Not in cache, try to fetch it
                    try:
                        tag = self.client.tags.get(tag_id)
                        tag_names.append(tag.name)
                        # Add to cache for future lookups
                        self._tag_cache[tag.name.lower()] = tag
                    except Exception as e:
                        logger.warning(f"Failed to resolve tag ID {tag_id}: {e}")
                        # If we can't resolve it, keep the ID as string
                        tag_names.append(str(tag_id))
            elif hasattr(tag_item, "name"):
                # It's a tag object
                tag_names.append(tag_item.name)
            else:
                # Unknown format, keep as string
                tag_names.append(str(tag_item))

        return tag_names

    def _entity_to_dict(self, entity: Entity, entity_type: str) -> dict[str, Any]:
        """
        Convert entity object to dictionary.

        Args:
            entity: Entity object
            entity_type: Our entity type string

        Returns:
            Dictionary representation
        """
        result: dict[str, Any] = {
            "id": entity.id,
            "entity_id": entity.entity_id,
            "name": entity.name,
            "entity_type": entity_type,
            "type": getattr(entity, "type", None),
            "tags": [],
            "created_at": (
                entity.created_at.isoformat()
                if hasattr(entity, "created_at") and entity.created_at
                else None
            ),
            "updated_at": (
                entity.updated_at.isoformat()
                if hasattr(entity, "updated_at") and entity.updated_at
                else None
            ),
        }

        # Handle visibility - translate is_private to is_hidden
        # Entities use is_private field
        is_private = getattr(entity, "is_private", None)
        if is_private is not None:
            result["is_hidden"] = is_private
        else:
            # Default to visible if no is_private field
            result["is_hidden"] = False

        # Convert HTML entry to Markdown
        if hasattr(entity, "entry") and entity.entry:
            result["entry"] = self.converter.html_to_markdown(entity.entry)
        else:
            result["entry"] = None

        # Extract tag names using helper method
        if hasattr(entity, "tags"):
            result["tags"] = self._resolve_tag_names(entity.tags)

        # Handle posts if present (when related=True)
        if hasattr(entity, "posts") and entity.posts is not None:
            result["posts"] = [self._post_to_dict(post) for post in entity.posts]

        # Handle quest-specific fields
        if entity_type == "quest":
            result["is_completed"] = getattr(entity, "is_completed", None)

        # Handle image fields - always include all 5 fields
        result["image"] = getattr(entity, "image", None)
        result["image_full"] = getattr(entity, "image_full", None)
        result["image_thumb"] = getattr(entity, "image_thumb", None)
        result["image_uuid"] = getattr(entity, "image_uuid", None)
        result["header_uuid"] = getattr(entity, "header_uuid", None)

        # Type-specific fields (price/size for items, date for events, ...)
        extra_fields = self._extra_fields(entity)
        if extra_fields:
            result["fields"] = extra_fields

        return result

    @staticmethod
    def _extra_fields(entity: Any) -> dict[str, Any]:
        """Collect non-empty type-specific fields of an entity model."""
        try:
            dumped = entity.model_dump()
        except Exception:
            return {}
        if not isinstance(dumped, dict):
            return {}

        fields: dict[str, Any] = {}
        for key, value in dumped.items():
            if key in _BASE_FIELD_NAMES or value is None:
                continue
            if isinstance(value, (list, dict, str)) and not value:
                continue
            if hasattr(value, "isoformat"):
                value = value.isoformat()
            fields[key] = value
        return fields

    def _post_to_dict(self, post: Any) -> dict[str, Any]:
        """
        Convert post object to dictionary.

        Args:
            post: Post object

        Returns:
            Dictionary representation
        """
        result = {
            "id": post.id,
            "name": post.name,
        }

        # Handle visibility - translate visibility_id to is_hidden
        # Posts use visibility_id field
        visibility_id = getattr(post, "visibility_id", None)
        if visibility_id is not None:
            # visibility_id 2 = admin only (hidden from players)
            result["is_hidden"] = visibility_id == 2
        else:
            # Default to visible if no visibility_id
            result["is_hidden"] = False

        # Convert HTML entry to Markdown
        if hasattr(post, "entry") and post.entry:
            result["entry"] = self.converter.html_to_markdown(post.entry)
        else:
            result["entry"] = None

        return result


# Global service instance (initialized on first use)
_service: KankaService | None = None


def get_service() -> KankaService:
    """Get or create the Kanka service instance."""
    global _service
    if _service is None:
        _service = KankaService()
    return _service

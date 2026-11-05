"""Typed owner-scoped Entity library payloads. Wire fields use API camelCase."""

from __future__ import annotations

from typing import Literal, TypedDict
from modelrunner_ai.entity_references import EntityKind, _uuid


class EntityImage(TypedDict):
    fileId: str
    url: str
    mimeType: str
    position: int


class EntityDisplayCover(TypedDict):
    fileId: str
    url: str
    mimeType: str


class _EntityOrganization(TypedDict, total=False):
    displayCover: EntityDisplayCover | None
    isFavorited: bool
    tags: list[dict[str, str]]


class Entity(_EntityOrganization):
    id: str
    type: EntityKind
    name: str
    handle: str
    description: str | None
    coverFileId: str
    version: int
    createdAt: str
    updatedAt: str
    files: list[EntityImage]


class _CreateRequired(TypedDict):
    type: EntityKind
    name: str
    handle: str
    fileIds: list[str]


class CreateEntityInput(_CreateRequired, total=False):
    # Characters require a nonempty description; the API validates stored-kind rules.
    description: str | None
    coverFileId: str
    displayCoverFileId: str | None


class _UpdateRequired(TypedDict):
    version: int


class UpdateEntityInput(_UpdateRequired, total=False):
    # Only non-character handles are editable.
    handle: str
    displayCoverFileId: str | None
    name: str
    description: str | None
    fileIds: list[str]
    coverFileId: str


class EntityCapabilities(TypedDict):
    contractVersion: Literal[1]
    writesEnabled: bool
    submissionsEnabled: bool
    newKindsEnabled: bool


class EntityList(TypedDict):
    items: list[Entity]
    counts: dict[EntityKind, int]
    total: int
    page: int
    limit: int
    capabilities: EntityCapabilities


class EntityHandleAvailability(TypedDict):
    handle: str
    available: bool


def entity_path(entity_id: str) -> str:
    if not _uuid(entity_id):
        raise ValueError("An entity UUID is required")
    return f"/{entity_id}"


def handle_input(handle: str | None, name: str | None) -> dict[str, str]:
    if (handle is None) == (name is None):
        raise ValueError("Provide exactly one of handle or name")
    return {"handle": handle} if handle is not None else {"name": name}  # type: ignore[dict-item]

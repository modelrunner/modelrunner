"""Explicit Entity bindings, validated before uploads or paid queue submission."""

from __future__ import annotations

import re
from typing import Any, Literal, Mapping, Sequence, TypedDict
from uuid import UUID

ENTITY_KINDS = ("character", "prop", "environment", "style", "scene")
EntityKind = Literal["character", "prop", "environment", "style", "scene"]


class _ReferenceRequired(TypedDict):
    id: str
    mention: str
    version: int


class _EntityReferenceRequired(_ReferenceRequired):
    type: EntityKind


class _CharacterReferenceRequired(_ReferenceRequired):
    type: Literal["character"]


class EntityReferenceBinding(_EntityReferenceRequired, total=False):
    file_ids: list[str]


class CharacterReferenceBinding(_CharacterReferenceRequired, total=False):
    file_ids: list[str]


def reject_raw_entity_references(arguments: Mapping[str, Any]) -> None:
    if "modelrunner_references" in arguments:
        raise ValueError(
            "Use entity_references= or character_references= instead of modelrunner_references in model arguments"
        )


def _uuid(value: Any) -> bool:
    try:
        return isinstance(value, str) and str(UUID(value)) == value.lower()
    except (ValueError, AttributeError):
        return False


def _copy_reference_bindings(
    references: (
        Sequence[EntityReferenceBinding] | Sequence[CharacterReferenceBinding] | None
    ),
    *,
    character_only: bool = False,
) -> list[dict[str, Any]] | None:
    if references is None:
        return None
    if not isinstance(references, (list, tuple)) or not 1 <= len(references) <= 8:
        raise ValueError("Select between one and eight entity references")
    result = []
    ids: set[str] = set()
    mentions: set[str] = set()
    for binding in references:
        if (
            not isinstance(binding, Mapping)
            or set(binding) - {"type", "id", "mention", "version", "file_ids"}
            or binding.get("type")
            not in (("character",) if character_only else ENTITY_KINDS)
            or not _uuid(binding.get("id"))
            or not isinstance(binding.get("mention"), str)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", binding["mention"])
            or re.fullmatch(r"(?:image|video|audio|element)\d+", binding["mention"])
            or type(binding.get("version")) is not int
            or binding["version"] < 1
            or binding["id"] in ids
            or binding["mention"] in mentions
        ):
            raise ValueError("Invalid or duplicate entity reference")
        selected = binding.get("file_ids")
        if "file_ids" in binding and (
            not isinstance(selected, (list, tuple))
            or not 1 <= len(selected) <= 20
            or not all(_uuid(value) for value in selected)
            or len(set(selected)) != len(selected)
        ):
            raise ValueError("Choose one to twenty unique reference image IDs")
        ids.add(binding["id"])
        mentions.add(binding["mention"])
        result.append(
            {
                **binding,
                **({"file_ids": list(selected)} if selected is not None else {}),
            }
        )
    return result


def reference_model_path(application: str, path: str) -> str:
    if path or not re.fullmatch(
        r"[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*", application
    ):
        raise ValueError(
            "Entity references require a direct model endpoint with no extra path"
        )
    if application.split("/")[0].lower() in {
        "characters",
        "entities",
        "models",
        "wrappers",
        "storage",
        "files",
        "keys",
        "tokens",
        "endpoints",
        "requests",
        "billing",
        "deployments",
        "gpus",
        "search",
        "collections",
        "tags",
        "webhooks",
        "retention",
        "internal",
    }:
        raise ValueError("Entity references require a direct model endpoint")
    return application


def require_character_reference_support(model: Any) -> None:
    capability = model.get("characterReferences") if isinstance(model, dict) else None
    if (
        not isinstance(capability, dict)
        or capability.get("contractVersion") != 1
        or capability.get("enabled") is not True
    ):
        raise ValueError(
            "This server/model does not accept character reference contract v1. Bindings were not submitted"
        )


def copy_entity_references(
    references: Sequence[EntityReferenceBinding] | None,
) -> list[dict[str, Any]] | None:
    return _copy_reference_bindings(references)


def copy_character_references(
    references: Sequence[CharacterReferenceBinding] | None,
) -> list[dict[str, Any]] | None:
    return _copy_reference_bindings(references, character_only=True)


def copy_selected_references(
    character_references: Sequence[CharacterReferenceBinding] | None,
    entity_references: Sequence[EntityReferenceBinding] | None,
) -> list[dict[str, Any]] | None:
    if character_references is not None and entity_references is not None:
        raise ValueError(
            "Use either entity_references or character_references, not both"
        )
    return (
        copy_entity_references(entity_references)
        if entity_references is not None
        else copy_character_references(character_references)
    )


def require_entity_reference_support(
    model: Any, bindings: Sequence[Mapping[str, Any]]
) -> None:
    if all(binding["type"] == "character" for binding in bindings):
        require_character_reference_support(model)
        return
    capability = model.get("entityReferences") if isinstance(model, dict) else None
    if (
        not isinstance(capability, dict)
        or type(capability.get("contractVersion")) is not int
        or capability.get("contractVersion") != 1
        or capability.get("enabled") is not True
        or not isinstance(capability.get("supportedKinds"), list)
        or capability.get("mode") not in ("image-list", "description-only")
        or type(capability.get("maxEntities")) is not int
        or capability["maxEntities"] < len(bindings)
        or any(
            binding["type"] not in capability["supportedKinds"] for binding in bindings
        )
    ):
        raise ValueError(
            "This server/model does not accept the selected entity kinds. Bindings were not submitted"
        )


def require_entity_owner_support(capability: Any) -> None:
    if (
        not isinstance(capability, dict)
        or type(capability.get("contractVersion")) is not int
        or capability.get("contractVersion") != 1
        or capability.get("newKindsEnabled") is not True
        or capability.get("submissionsEnabled") is not True
    ):
        raise ValueError(
            "Entity submissions are not enabled for this account. Bindings were not submitted"
        )

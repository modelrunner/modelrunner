"""Explicit queue bindings; validation never uploads or generates media."""
from __future__ import annotations

import re
from typing import Any, Literal, Mapping, Sequence, TypedDict
from uuid import UUID


class _CharacterReferenceRequired(TypedDict):
    type: Literal["character"]
    id: str
    mention: str
    version: int


class CharacterReferenceBinding(_CharacterReferenceRequired, total=False):
    file_ids: list[str]


def reject_raw_character_references(arguments: Mapping[str, Any]) -> None:
    if "modelrunner_references" in arguments:
        raise ValueError("Use character_references= instead of modelrunner_references in model arguments")


def _uuid(value: Any) -> bool:
    try:
        return isinstance(value, str) and str(UUID(value)) == value.lower()
    except (ValueError, AttributeError):
        return False


def copy_character_references(references: Sequence[CharacterReferenceBinding] | None) -> list[dict[str, Any]] | None:
    if references is None:
        return None
    if not isinstance(references, (list, tuple)) or not 1 <= len(references) <= 8:
        raise ValueError("Select between one and eight character references")
    result = []
    ids: set[str] = set()
    mentions: set[str] = set()
    for binding in references:
        if (not isinstance(binding, Mapping) or set(binding) - {"type", "id", "mention", "version", "file_ids"}
            or binding.get("type") != "character" or not _uuid(binding.get("id"))
            or not isinstance(binding.get("mention"), str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", binding["mention"])
            or re.fullmatch(r"(?:image|video|audio|element)\d+", binding["mention"])
            or type(binding.get("version")) is not int or binding["version"] < 1
            or binding["id"] in ids or binding["mention"] in mentions):
            raise ValueError("Invalid or duplicate character reference")
        selected = binding.get("file_ids")
        if "file_ids" in binding and (not isinstance(selected, (list, tuple)) or not 1 <= len(selected) <= 20 or not all(_uuid(value) for value in selected) or len(set(selected)) != len(selected)):
            raise ValueError("Choose one to twenty unique character image IDs")
        ids.add(binding["id"])
        mentions.add(binding["mention"])
        result.append({**binding, **({"file_ids": list(selected)} if selected is not None else {})})
    return result


def character_model_path(application: str, path: str) -> str:
    if path or not re.fullmatch(r"[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*", application):
        raise ValueError("Character references require a direct model endpoint with no extra path")
    if application.split("/")[0].lower() in {"characters", "models", "wrappers", "storage", "files", "keys", "tokens", "endpoints", "requests", "billing", "deployments", "gpus", "search", "collections", "tags", "webhooks", "retention", "internal"}:
        raise ValueError("Character references require a direct model endpoint")
    return application


def require_character_reference_support(model: Any) -> None:
    capability = model.get("characterReferences") if isinstance(model, dict) else None
    if not isinstance(capability, dict) or capability.get("contractVersion") != 1 or capability.get("enabled") is not True:
        raise ValueError("This server/model does not accept character reference contract v1. Bindings were not submitted")

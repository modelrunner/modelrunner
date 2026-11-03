"""Character-only compatibility imports for the shared Entity binding implementation."""

from modelrunner_ai.entity_references import (
    CharacterReferenceBinding,
    copy_character_references,
    reference_model_path as character_model_path,
    reject_raw_entity_references as reject_raw_character_references,
    require_character_reference_support,
)

__all__ = [
    "CharacterReferenceBinding",
    "copy_character_references",
    "character_model_path",
    "reject_raw_character_references",
    "require_character_reference_support",
]

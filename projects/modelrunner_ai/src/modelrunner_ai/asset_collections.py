"""Private asset organization types. All timestamps and wire keys match the API."""
from __future__ import annotations
from typing import Literal, TypedDict, Union
from modelrunner_ai.entities import entity_path

class AssetTag(TypedDict):
    id: str
    name: str

class _RuleRequired(TypedDict):
    field: Literal["model", "source", "createdAt", "mediaType", "tag", "favorite", "entity"]
    value: Union[str, bool]

class AssetRuleCondition(_RuleRequired, total=False):
    operator: Literal["before", "after"]

class AssetRuleGroup(TypedDict):
    # The API bounds this tree to three group levels and twenty conditions.
    match: Literal["all", "any"]
    rules: list[Union[AssetRuleCondition, "AssetRuleGroup"]]

class AssetCollection(TypedDict):
    id: str
    name: str
    description: str | None
    kind: Literal["manual", "smart"]
    parentId: str | None
    version: int
    rules: AssetRuleGroup | None
    isFavorited: bool
    tags: list[AssetTag]
    createdAt: str
    updatedAt: str

class _CreateCollectionRequired(TypedDict):
    # Retain this UUID to reconcile an uncertain create outcome.
    operationId: str
    name: str
    kind: Literal["manual", "smart"]

class CreateAssetCollectionInput(_CreateCollectionRequired, total=False):
    description: str | None
    parentId: str | None
    rules: AssetRuleGroup | None

class _UpdateCollectionRequired(TypedDict):
    version: int

class UpdateAssetCollectionInput(_UpdateCollectionRequired, total=False):
    name: str
    description: str | None
    parentId: str | None
    rules: AssetRuleGroup

class AssetFile(TypedDict):
    id: str
    url: str
    thumbnailUrl: str | None
    userId: str | None
    mimeType: str | None
    sizeBytes: int | None
    storageKey: str | None
    source: Literal["upload", "request_output", "external"]
    createdAt: str
    isFavorited: bool
    tags: list[AssetTag]

class AssetGalleryItem(AssetFile):
    generated: bool
    manuallyLinked: bool
    requestId: str | None
    modelEndpoint: str | None

class AssetGallery(TypedDict):
    items: list[AssetGalleryItem]
    total: int
    page: int
    limit: int

class ListAssetFilesOptions(TypedDict, total=False):
    page: int
    limit: int
    q: str
    mimeTypePrefix: str
    order: Literal["newest", "oldest"]
    sources: list[Literal["upload", "output", "input", "external"]]
    tagIds: list[str]
    tagRule: Literal["all", "any"]
    favoritesOnly: bool
    modelEndpoint: str
    createdAfter: str
    createdBefore: str

class AssetFilePagination(TypedDict):
    page: int
    limit: int
    total: int
    totalPages: int

class AssetFileList(TypedDict):
    items: list[AssetFile]
    pagination: AssetFilePagination

class AssetCollectionList(TypedDict):
    items: list[AssetCollection]

class AssetRulePreview(TypedDict):
    valid: bool
    total: int
    invalid: list[str]

class AssetTagInput(TypedDict, total=False):
    tagId: str
    name: str


def collection_path(collection_id: str) -> str:
    return "/asset-collections" + entity_path(collection_id)

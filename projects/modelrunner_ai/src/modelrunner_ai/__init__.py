from modelrunner_ai.entities import (
    Entity, EntityImage, CreateEntityInput, UpdateEntityInput, EntityList,
    EntityCapabilities, EntityHandleAvailability,
)
from modelrunner_ai.character_references import CharacterReferenceBinding
from modelrunner_ai.entity_references import EntityKind, EntityReferenceBinding
from modelrunner_ai.client import (
    AsyncClient,
    SyncClient,
    Status,
    Queued,
    InProgress,
    Completed,
    SyncRequestHandle,
    AsyncRequestHandle,
    encode,
    encode_file,
    encode_image,
)
from modelrunner_ai.metadata import (
    REQUEST_METADATA_MAX_KEYS,
    validate_metadata,
)
from modelrunner_ai.storage import (
    EXPIRATION_VALUES,
    OBJECT_LIFECYCLE_PREFERENCE_HEADER,
    STORE_IO_HEADER,
    StorageSettings,
)
from modelrunner_ai.webhooks import (
    WEBHOOK_EVENTS,
    WebhookVerificationError,
    verify_webhook,
)

__all__ = [
    "Entity",
    "EntityImage",
    "CreateEntityInput",
    "UpdateEntityInput",
    "EntityList",
    "EntityCapabilities",
    "EntityHandleAvailability",
    "list_entities",
    "get_entity",
    "create_entity",
    "update_entity",
    "delete_entity",
    "check_entity_handle",
    "get_entity_capabilities",
    "list_entities_async",
    "get_entity_async",
    "create_entity_async",
    "update_entity_async",
    "delete_entity_async",
    "check_entity_handle_async",
    "get_entity_capabilities_async",

    "CharacterReferenceBinding",
    "EntityKind",
    "EntityReferenceBinding",
    "SyncClient",
    "AsyncClient",
    "Status",
    "Queued",
    "InProgress",
    "Completed",
    "SyncRequestHandle",
    "AsyncRequestHandle",
    "REQUEST_METADATA_MAX_KEYS",
    "validate_metadata",
    "StorageSettings",
    "EXPIRATION_VALUES",
    "OBJECT_LIFECYCLE_PREFERENCE_HEADER",
    "STORE_IO_HEADER",
    "WEBHOOK_EVENTS",
    "WebhookVerificationError",
    "verify_webhook",
    "get_webhook_secret",
    "get_webhook_secret_async",
    "rotate_webhook_secret",
    "rotate_webhook_secret_async",
    "run",
    "subscribe_async",
    "subscribe",
    "submit",
    "stream",
    "run_async",
    "submit_async",
    "stream_async",
    "cancel",
    "cancel_async",
    "status",
    "status_async",
    "result",
    "result_async",
    "encode",
    "encode_file",
    "encode_image",
]

sync_client = SyncClient()
run = sync_client.run
subscribe = sync_client.subscribe
submit = sync_client.submit
status = sync_client.status
result = sync_client.result
cancel = sync_client.cancel
stream = sync_client.stream
upload = sync_client.upload
upload_file = sync_client.upload_file
upload_image = sync_client.upload_image
get_webhook_secret = sync_client.get_webhook_secret
rotate_webhook_secret = sync_client.rotate_webhook_secret

async_client = AsyncClient()
run_async = async_client.run
subscribe_async = async_client.subscribe
submit_async = async_client.submit
status_async = async_client.status
result_async = async_client.result
cancel_async = async_client.cancel
stream_async = async_client.stream
upload_async = async_client.upload
upload_file_async = async_client.upload_file
upload_image_async = async_client.upload_image
get_webhook_secret_async = async_client.get_webhook_secret
rotate_webhook_secret_async = async_client.rotate_webhook_secret

list_entities = sync_client.list_entities
list_entities_async = async_client.list_entities
get_entity = sync_client.get_entity
get_entity_async = async_client.get_entity
create_entity = sync_client.create_entity
create_entity_async = async_client.create_entity
update_entity = sync_client.update_entity
update_entity_async = async_client.update_entity
delete_entity = sync_client.delete_entity
delete_entity_async = async_client.delete_entity
check_entity_handle = sync_client.check_entity_handle
check_entity_handle_async = async_client.check_entity_handle
get_entity_capabilities = sync_client.get_entity_capabilities
get_entity_capabilities_async = async_client.get_entity_capabilities

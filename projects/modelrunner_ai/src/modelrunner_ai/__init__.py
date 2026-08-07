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
from modelrunner_ai.webhooks import (
    WEBHOOK_EVENTS,
    WebhookVerificationError,
    verify_webhook,
)

__all__ = [
    "SyncClient",
    "AsyncClient",
    "Status",
    "Queued",
    "InProgress",
    "Completed",
    "SyncRequestHandle",
    "AsyncRequestHandle",
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

# modelrunner.ai Python client

This is a Python client library for interacting with ML models deployed on [modelrunner.ai](https://modelrunner.ai).

## Getting started

To install the client, run:

```bash
pip install modelrunner-ai
```

To use the client, you need to have an API key. You can get one by signing up at [modelrunner.ai](https://modelrunner.ai). Once you have it, set
it as an environment variable:

```bash
export MODELRUNNER_KEY=your-api-key
```

Now you can use the client to interact with your models. Here's an example of how to use it:


```python
import asyncio
import modelrunner_ai

async def main():
    response = await modelrunner_ai.submit_async("bytedance/sdxl-lightning-4step", arguments={"prompt": "two friends cooking together"})

    logs_index = 0
    async for event in response.iter_events(with_logs=True):
        if isinstance(event, modelrunner_ai.Queued):
            print("Queued. Position:", event.position)
        elif isinstance(event, (modelrunner_ai.InProgress, modelrunner_ai.Completed)):
            new_logs = event.logs[logs_index:]
            for log in new_logs:
                print(log["message"])
            logs_index = len(event.logs)

    result = await response.get()
    print(result["output"])


asyncio.run(main())
```

## Running a model

`run` is the one-liner version: it submits the request, waits for the inference to finish and hands back the result.

```python
import modelrunner_ai

result = modelrunner_ai.run(
    "bytedance/sdxl-lightning-4step",
    arguments={"prompt": "two friends cooking together"},
)
print(result["output"])
```

It blocks for as long as the model takes, so reach for `submit` (above) or a webhook when that is minutes rather than seconds. `timeout` bounds the enqueue request only, not the wait.

`result` is the same record `submit` + `handle.get()` returns, so the failure rule is the same one the webhook section spells out below: a failed generation still reads `status: "COMPLETED"`, with the detail in `error` and nothing useful in `output`.

> **Changed in 0.4.0.** `run` used to return the queue envelope — `{"status": "IN_PROGRESS", "request_id": ..., ...}` — the instant the request was accepted, so the `result["output"]` its own docstring promised raised `KeyError`. It now waits, as documented. Code that worked around this by taking that envelope and polling its urls by hand breaks: there is no envelope to read any more. Use `submit` + `handle.get()`, which is unchanged, or drop the workaround and let `run` do the waiting.

## Uploading files

If the model requires files as input, you can upload them directly to media.modelrunner.ai (our CDN) and pass the URLs to the client. Here's an example:

```python
import modelrunner_ai

input_image = modelrunner_ai.upload_file("./image.jpg")
print(input_image)
response = modelrunner_ai.run("swook/inspyrenet", arguments={"image_path": input_image})
print(response["output"])
```

## Tagging requests with metadata

Attach a flat map of your own string tags to a request — job ids, environments, batch labels — with `metadata`. It is stored on the request and never sent to the model.

```python
import modelrunner_ai

result = modelrunner_ai.run(
    "bytedance/sdxl-lightning-4step",
    arguments={"prompt": "two friends cooking together"},
    metadata={"project": "onboarding-demo", "env": "prod"},
)
```

Supported on `run`, `submit`, `subscribe` and `stream` (and their `_async` variants). The limits are checked locally before the request is sent, with every violation reported at once:

| Constraint | Value |
| --- | --- |
| Max keys | 16 |
| Key length | 1–64 characters |
| Values | strings only, ≤512 characters |

> **New in 0.3.0.** Earlier versions did not accept `metadata` at all — passing it raised `TypeError`.

`metadata` is reserved at the top level of the request body, so a model whose own input schema declares a `metadata` field cannot receive it this way.

## Webhooks

Instead of polling a handle, you can have `modelrunner.ai` call you back. This is the only option that survives a restart on either side, which is what makes it the right choice for multi-minute video and training jobs.

```python
import modelrunner_ai

handle = modelrunner_ai.submit(
    "bytedance/sdxl-lightning-4step",
    arguments={"prompt": "two friends cooking together"},
    webhook_url="https://example.com/webhooks/modelrunner",
    # optional — defaults to ["completed"]
    webhook_events=["start", "completed"],
)
```

`start` is best effort: a fast request can go straight from `IN_QUEUE` to `COMPLETED` between two polls, in which case only `completed` is delivered. Never block waiting for `start`.

> **Changed in 0.3.0.** `webhook_url` was previously accepted and **silently ignored** — it was sent as a query parameter the API does not read, so no callback was ever made. It is now sent correctly, which also means it is now validated: a value carried over from before (one over 2048 characters, say) turns a submit that used to succeed into an error.

### Verifying a delivery

Every delivery is signed with [Standard Webhooks](https://www.standardwebhooks.com). Fetch your secret **once** and keep it in your receiver's environment:

```python
secret = modelrunner_ai.get_webhook_secret()
```

Then verify each delivery against the **raw** request body — the signature covers the delivered bytes, so a body that has been parsed and re-serialized will not verify:

```python
import os
from fastapi import FastAPI, Request, Response
from modelrunner_ai import WebhookVerificationError, verify_webhook

app = FastAPI()
SECRET = os.environ["MODELRUNNER_WEBHOOK_SECRET"]

@app.post("/webhooks/modelrunner")
async def receive(request: Request):
    try:
        payload = verify_webhook(
            SECRET,
            request.headers,
            await request.body(),  # raw bytes, before any JSON parsing
        )
    except WebhookVerificationError:
        return Response(status_code=401)

    handle(payload)
    return Response(status_code=200)
```

In Flask the raw body is `request.get_data()`.

`verify_webhook` raises `WebhookVerificationError` on a missing header, a timestamp outside the 5-minute tolerance, or a signature that does not match. Treat every case the same way and never branch on the message.

### What your endpoint must do

- **Respond `2xx` directly.** Redirects are never followed, so a `301` — a missing trailing slash, an `http`→`https` upgrade, a `www.` canonicalization — is recorded as a *failed* attempt and you will see nothing but silence.
- **Deduplicate on the `webhook-id` header.** Delivery is at-least-once and that id is stable across retries.
- A failed attempt is retried on a fixed schedule, roughly 10 times over 2 hours. Reply **`410 Gone`** to stop delivery permanently.
- Acknowledge before doing slow work. The attempt has a timeout, and a slow `200` is a failed attempt.

### Reading the payload

The payload is the same object the result endpoint returns, plus `event` and `billingStatus`. Timestamps are ISO-8601 strings.

> 🚨 **`status` alone cannot tell success from failure.** A failed generation is normalized to `status: "COMPLETED"` with `billingStatus: "failed"`. Code that keys off `status` reads every failure as a success — use `billingStatus`.

```python
if payload["event"] == "completed" and payload["billingStatus"] != "failed":
    print(payload["output"])
```

`input` is replaced by `{"_elided": "..."}` when it serializes to more than 64KB; fetch the request itself in that case.

### Rotating the secret

```python
secret = modelrunner_ai.rotate_webhook_secret()
```

Both the old and the new secret are signed with for **24 hours** afterwards, so you have that long to deploy the new value. `verify_webhook` accepts a list of secrets to bridge the gap. Rotating twice inside that window ends it early and breaks receivers still holding the original secret, so this call is never retried automatically.



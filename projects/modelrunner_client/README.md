# modelrunner.ai Python client

This is a Python client library for interacting with ML models deployed on [modelrunner.ai](https://modelrunner.ai).

## Getting started

To install the client, run:

```bash
pip install modelrunner-client
```

To use the client, you need to have an API key. You can get one by signing up at [modelrunner.ai](https://modelrunner.ai). Once you have it, set
it as an environment variable:

```bash
export MODELRUNNER_KEY=your-api-key
```

Now you can use the client to interact with your models. Here's an example of how to use it:


```python
import asyncio
import modelrunner_client

async def main():
    response = await modelrunner_client.run_async("bytedance/sdxl-lightning-4step", arguments={"prompt": "two friends cooking together"})
    print(response["images"][0]["url"])


asyncio.run(main())
```

## Uploading files

If the model requires files as input, you can upload them directly to media.modelrunner.ai (our CDN) and pass the URLs to the client. Here's an example:

```python
import modelrunner_client

input_audio = modelrunner_client.upload_file("path/to/audio.wav")
response = modelrunner_client.run("meta/musicgen", arguments={"input_audio": input_audio})
print(response["text"])
```


## Queuing requests

When you want to send a request and keep receiving updates on its status, you can use the `submit` method. Here's an example:

```python
import asyncio
import modelrunner_client

async def main():
    response = await modelrunner_client.submit_async("bytedance/sdxl-lightning-4step", arguments={"prompt": "two friends cooking together"})

    logs_index = 0
    async for event in response.iter_events(with_logs=True):
        if isinstance(event, modelrunner_client.Queued):
            print("Queued. Position:", event.position)
        elif isinstance(event, (modelrunner_client.InProgress, modelrunner_client.Completed)):
            new_logs = event.logs[logs_index:]
            for log in new_logs:
                print(log["message"])
            logs_index = len(event.logs)

    result = await response.get()
    print(result["images"][0]["url"])


asyncio.run(main())
```


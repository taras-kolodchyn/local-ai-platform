"""Local alias settings on Moyai's native Responses model and accounting transport."""
import asyncio
from email.utils import parsedate_to_datetime
import json
import math
import time

from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.litellm import LiteLLMProvider
from moyai.inference_usage import InferenceUsageTransport


class LocalTransport(InferenceUsageTransport):
    async def handle_async_request(self, request):
        if request.method == 'POST' and request.url.path.endswith('/responses'):
            payload = json.loads(request.content)
            tools = payload.get('tools', [])
            print(json.dumps({'event': 'local_model_request', 'stream': payload.get('stream'),
                              'parallel_tool_calls': payload.get('parallel_tool_calls'),
                              'tool_count': len(tools)}),
                  flush=True)
        if request.method != 'POST' or not request.url.path.rstrip('/').endswith(('/responses','/chat/completions')):
            return await super().handle_async_request(request)
        deadline = time.monotonic() + 90
        delay = 5
        while True:
            response = await super().handle_async_request(request)
            if response.status_code != 429 or b'max_parallel_requests' not in await response.aread():
                return response
            cooldown = response.headers.get('retry-after')
            wait = delay
            if cooldown:
                try:
                    wait = float(cooldown)
                except ValueError:
                    try:
                        wait = parsedate_to_datetime(cooldown).timestamp() - time.time()
                    except (ValueError, TypeError, OverflowError):
                        wait = delay
            if not math.isfinite(wait):
                return response
            wait = max(delay, wait)
            if time.monotonic() + wait >= deadline:
                return response
            # A concurrency rejection precedes generation. Each attempt goes through native accounting.
            await asyncio.sleep(wait)
            delay = min(20, delay * 2)


def local_model(settings):
    client = AsyncOpenAI(
        base_url=settings.gateway_url, api_key=settings.gateway_key.get_secret_value(),
        max_retries=0, timeout=float(settings.model_timeout_seconds),
        http_client=DefaultAsyncHttpxClient(transport=LocalTransport(),
                                          headers={'Accept-Encoding': 'identity'},
                                          follow_redirects=False, trust_env=False),
    )
    model = OpenAIResponsesModel(
        'local-qwen', provider=LiteLLMProvider(openai_client=client),
        settings={'parallel_tool_calls': False, 'temperature': 0},
    )
    return model, client

"""Local alias settings on Moyai's native Responses model and accounting transport."""
import json

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
        return await super().handle_async_request(request)


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

import unittest
from unittest.mock import AsyncMock, patch

import httpx
from inference import LocalTransport


class InferenceCapacityTests(unittest.IsolatedAsyncioTestCase):
    async def send(self, responses):
        requests = []
        def upstream(request):
            requests.append(request.content)
            return responses[len(requests) - 1]
        with patch('asyncio.sleep', new_callable=AsyncMock) as wait:
            async with httpx.AsyncClient(transport=LocalTransport(httpx.MockTransport(upstream))) as client:
                result = await client.post('http://localhost/v1/responses', json={'input':'example'})
        return result, requests, wait

    async def test_parallel_limit_waits_before_resubmitting_the_unadmitted_request(self):
        busy = httpx.Response(429, headers={'retry-after':'12'}, json={'error':{'message':'Limit type: max_parallel_requests.'}})
        response, requests, wait = await self.send([busy, httpx.Response(200, json={'id':'result'})])
        self.assertEqual(response.json(), {'id':'result'})
        self.assertEqual(requests, [requests[0],requests[0]])
        wait.assert_awaited_once_with(12)

    async def test_budget_refusal_server_error_and_long_cooldown_are_returned(self):
        for status, message, headers in [(429,'budget exceeded',{}),(503,'unavailable',{}),
                                         (429,'max_parallel_requests',{'retry-after':'3600'})]:
            with self.subTest(status=status, message=message):
                response, requests, wait = await self.send([httpx.Response(status, headers=headers,
                                                          json={'error':{'message':message}})])
                self.assertEqual(response.status_code,status)
                self.assertEqual(len(requests),1)
                wait.assert_not_awaited()

"""Compare actual local model input usage and answer fidelity with Headroom."""
import json
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]


def payload(route):
    instruction = {"role": "user", "content": "Read the worker log and report the error code."}
    question = {"role": "user", "content": "Reply with only the exact error code from the log."}
    lines = [f"2026-11-18 10:{i // 60:02}:{i % 60:02} INFO worker - completed health check successfully"
             for i in range(240)]
    lines[113] = "2026-11-18 10:01:53 ERROR worker - failed to acquire lock error_code=HMX731"
    log = "\n".join(lines)
    call = {"name": "read_log", "arguments": "{}"}
    acknowledged = {"role": "assistant", "content": "I have read the log."}
    body = {"model": "local-qwen", "temperature": 0}
    if route == "responses":
        body.update(max_output_tokens=32, store=False, input=[
            instruction, {"type": "function_call", "call_id": "call_1", **call},
            {"type": "function_call_output", "call_id": "call_1", "output": log}, acknowledged, question,
        ])
    else:
        body.update(max_tokens=32, messages=[
            instruction, {"role": "assistant", "tool_calls": [
                {"type": "function", "id": "call_1", "function": call}]},
            {"role": "tool", "tool_call_id": "call_1", "content": log}, acknowledged, question,
        ])
    return body


def probe(route, key, compressed):
    body = payload(route)
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "Cache-Control": "no-cache, no-store"}
    if not compressed:
        headers["x-headroom-bypass"] = "true"
    request = Request(f"http://127.0.0.1:4000/v1/{route}", data=json.dumps(body).encode(),
                      headers=headers)
    with urlopen(request, timeout=180) as response:
        result = json.load(response)
    if route == "responses":
        answer = "".join(part["text"] for item in result["output"] if item["type"] == "message"
                         for part in item["content"] if part["type"] == "output_text")
        tokens = result["usage"]["input_tokens"]
    else:
        answer = result["choices"][0]["message"]["content"]
        tokens = result["usage"]["prompt_tokens"]
    if answer.strip() != "HMX731":
        raise SystemExit(f"{route} (compression={compressed}): expected error code was not returned")
    return tokens


if __name__ == "__main__":
    key = (ROOT / ".local/litellm-api-key").read_text().strip()
    for route in ("chat/completions", "responses"):
        before = probe(route, key, False)
        after = probe(route, key, True)
        print(f"{route}: {before} -> {after} input tokens, exact error code preserved", flush=True)
        if not 0 < after < before:
            raise SystemExit(f"No measured input reduction on {route}")

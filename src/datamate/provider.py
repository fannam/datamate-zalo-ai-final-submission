from __future__ import annotations

import asyncio
import json
import time

import httpx
from pydantic import BaseModel

from .config import ModelSettings


class ModelError(RuntimeError):
    pass


class Provider:
    def __init__(self, settings: ModelSettings):
        self.settings = settings
        self.client = httpx.AsyncClient(timeout=settings.timeout, follow_redirects=False)

    async def generate(self, messages: list[dict], schema: type[BaseModel], *, thinking: bool | None = None, max_tokens: int | None = None, timeout: float | None = None, seed: int = 42, temperature: float | None = None):
        """Per-call overrides let one role (the verifier) think while the planner stays fast."""
        settings = self.settings
        body = {
            "model": settings.model, "messages": messages, "temperature": settings.temperature if temperature is None else temperature,
            "top_p": 0.95, "presence_penalty": 0.5, "seed": seed,
            "max_tokens": max_tokens or settings.max_tokens, "stream": settings.stream,
            "chat_template_kwargs": {"enable_thinking": settings.thinking if thinking is None else thinking},
            "response_format": {"type": "json_schema", "json_schema": {"name": schema.__name__, "strict": True, "schema": schema.model_json_schema()}},
        }
        if "openrouter.ai" in settings.base_url:
            # OpenRouter ignores chat_template_kwargs; its own switch decides whether the model reasons.
            body["reasoning"] = {"enabled": bool(body["chat_template_kwargs"]["enable_thinking"])}
        if settings.stream:
            body["stream_options"] = {"include_usage": True}
        if body["chat_template_kwargs"]["enable_thinking"] and settings.thinking_temperature is not None:
            # Greedy decoding makes Qwen's thinking loop ("Wait, I need to check ...") until the token limit;
            # Qwen's recommended thinking sampler, with the fixed seed above.
            body.update(temperature=settings.thinking_temperature, top_k=20)
        start = time.monotonic()
        attempts = []
        for attempt in range(settings.retries + 1):
            try:
                status, data, text = await self._request(body, timeout or settings.timeout)
                attempts.append({"status": status})
                if status in (429, 500, 502, 503, 504) and attempt < settings.retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                if status != 200:
                    message = text[:1500].replace(settings.api_key, "<redacted>")
                    raise ModelError(f"API HTTP {status}: {message}")
                choice = data["choices"][0]
                message = choice["message"]
                trace = {"response_model": data.get("model"), "usage": data.get("usage", {}), "finish_reason": choice.get("finish_reason"),
                         "content": message.get("content"), "thinking": message.get("reasoning_content", message.get("reasoning")),
                         "elapsed_ms": int((time.monotonic() - start) * 1000), "attempts": attempts}
                if choice.get("finish_reason") == "length":
                    trace["error"] = "output_token_limit"
                    return None, trace
                content = message.get("content") or ""
                if "</think>" in content:
                    content = content.split("</think>", 1)[1].strip()
                try:
                    parsed = schema.model_validate_json(content)
                    return parsed, trace
                except (ValueError, TypeError) as error:
                    trace["error"] = "invalid_schema: " + str(error)[:1600]
                    return None, trace
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                attempts.append({"error": type(error).__name__})
                if attempt == settings.retries:
                    raise ModelError(type(error).__name__) from None
                await asyncio.sleep(2 ** attempt)
        raise ModelError("request retry budget exhausted")

    async def _request(self, body: dict, timeout: float):
        """One HTTP attempt -> (status, completion dict, error text). Streaming keeps a long thinking call alive
        through gateways that cut idle non-streaming requests (the dev API answers 504 after ~180 s)."""
        url = self.settings.base_url + "/chat/completions"
        headers = {"Authorization": "Bearer " + self.settings.api_key}
        if not body.get("stream"):
            response = await self.client.post(url, json=body, headers=headers, timeout=timeout)
            return response.status_code, response.json() if response.status_code == 200 else None, response.text
        async with self.client.stream("POST", url, json=body, headers=headers, timeout=timeout) as response:
            if response.status_code != 200:
                return response.status_code, None, (await response.aread()).decode(errors="replace")
            content, reasoning, finish, usage, model = [], [], None, {}, None
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                chunk = json.loads(payload)
                model = chunk.get("model") or model
                usage = chunk.get("usage") or usage
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        content.append(delta["content"])
                    if delta.get("reasoning_content") or delta.get("reasoning"):
                        reasoning.append(delta.get("reasoning_content") or delta.get("reasoning"))
                    finish = choice.get("finish_reason") or finish
            message = {"content": "".join(content), "reasoning_content": "".join(reasoning) or None}
            return 200, {"model": model, "usage": usage, "choices": [{"finish_reason": finish, "message": message}]}, ""

    async def close(self):
        await self.client.aclose()

import asyncio
import json

import httpx
from pydantic import BaseModel

from datamate.config import ModelSettings
from datamate.provider import Provider


class Answer(BaseModel):
    ok: bool


def sse(*chunks):
    return "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"


def provider(handler, **settings):
    item = Provider(ModelSettings(base_url="https://example.invalid/v1", model="m", api_key="secret-key", retries=1, **settings))
    item.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return item


def test_streamed_completion_reassembles_content_thinking_and_usage():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        body = sse({"model": "m", "choices": [{"delta": {"reasoning_content": "nghĩ "}}]},
                   {"choices": [{"delta": {"reasoning_content": "tiếp"}}]},
                   {"choices": [{"delta": {"content": '{"ok": '}}]},
                   {"choices": [{"delta": {"content": "true}"}, "finish_reason": "stop"}]},
                   {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 7}})
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    item = provider(handler, stream=True)
    parsed, trace = asyncio.run(item.generate([{"role": "user", "content": "x"}], Answer, thinking=True))
    assert parsed == Answer(ok=True)
    assert trace["thinking"] == "nghĩ tiếp" and trace["finish_reason"] == "stop" and trace["usage"]["completion_tokens"] == 7
    assert seen["body"]["stream"] is True and seen["body"]["stream_options"] == {"include_usage": True}


def test_streamed_length_cut_and_gateway_error_are_reported():
    def cut(request):
        return httpx.Response(200, text=sse({"choices": [{"delta": {"content": '{"ok"'}, "finish_reason": "length"}]}))

    parsed, trace = asyncio.run(provider(cut, stream=True).generate([{"role": "user", "content": "x"}], Answer))
    assert parsed is None and trace["error"] == "output_token_limit"

    def gateway(request):
        return httpx.Response(504, text="504 Gateway Time-out secret-key")

    try:
        asyncio.run(provider(gateway, stream=True).generate([{"role": "user", "content": "x"}], Answer))
        raise AssertionError("expected ModelError")
    except Exception as error:
        assert "504" in str(error) and "secret-key" not in str(error)          # key never leaks into errors


def test_non_streamed_path_unchanged():
    def handler(request):
        assert json.loads(request.content)["stream"] is False
        return httpx.Response(200, json={"model": "m", "usage": {}, "choices": [{"finish_reason": "stop", "message": {"content": '{"ok": false}'}}]})

    parsed, _ = asyncio.run(provider(handler).generate([{"role": "user", "content": "x"}], Answer))
    assert parsed == Answer(ok=False)

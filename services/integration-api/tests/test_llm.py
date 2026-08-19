"""LLMClient providers: Gemini over httpx.MockTransport, Bedrock over an
injected fake boto3 client (boto3 itself is never imported in tests)."""
import json

import httpx
import pytest

from agent.config import AgentConfig
from agent.errors import BudgetExceededError, LLMConfigError, LLMUnavailableError
from agent.llm import BedrockClient, GeminiClient, make_llm_client

pytestmark = pytest.mark.anyio


def gemini_body(text, finish="STOP"):
    return {
        "candidates": [
            {"content": {"parts": [{"text": text}]}, "finishReason": finish}
        ]
    }


def make_gemini(handler, **kw):
    return GeminiClient(
        model="gemini-test",
        api_key="test-key",
        max_tokens=4096,
        retry_delay=0,
        transport=httpx.MockTransport(handler),
        **kw,
    )


async def test_gemini_happy_path_plain():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-goog-api-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=gemini_body("hello"))

    client = make_gemini(handler)
    assert await client.complete("hi") == "hello"
    assert "gemini-test:generateContent" in seen["url"]
    assert seen["key"] == "test-key"
    cfg = seen["body"]["generationConfig"]
    assert cfg["maxOutputTokens"] == 4096
    assert cfg["temperature"] == 0
    assert "responseMimeType" not in cfg


async def test_gemini_json_mode_sets_mime_type():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=gemini_body("{}"))

    await make_gemini(handler).complete("hi", json_mode=True)
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"


async def test_gemini_base_url_override():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=gemini_body("x"))

    await make_gemini(handler, base_url="http://llm.local").complete("hi")
    assert seen["url"].startswith("http://llm.local/")


async def test_gemini_multiple_parts_joined():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"parts": [{"text": "a"}, {"text": "b"}]},
                        "finishReason": "STOP",
                    }
                ]
            },
        )

    assert await make_gemini(handler).complete("hi") == "ab"


async def test_gemini_max_tokens_is_budget_exceeded():
    def handler(request):
        return httpx.Response(200, json=gemini_body("trunc", finish="MAX_TOKENS"))

    with pytest.raises(BudgetExceededError):
        await make_gemini(handler).complete("hi")


async def test_gemini_5xx_retried_once_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="overloaded")
        return httpx.Response(200, json=gemini_body("ok"))

    assert await make_gemini(handler).complete("hi") == "ok"
    assert calls["n"] == 2


async def test_gemini_5xx_twice_is_llm_unavailable():
    def handler(request):
        return httpx.Response(500, text="boom")

    with pytest.raises(LLMUnavailableError):
        await make_gemini(handler).complete("hi")


async def test_gemini_4xx_fails_immediately_without_retry():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(403, text="bad key")

    with pytest.raises(LLMUnavailableError):
        await make_gemini(handler).complete("hi")
    assert calls["n"] == 1


async def test_gemini_transport_error_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ConnectError("refused")

    with pytest.raises(LLMUnavailableError):
        await make_gemini(handler).complete("hi")
    assert calls["n"] == 2


async def test_gemini_malformed_body_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"nothing": True})
        return httpx.Response(200, json=gemini_body("ok"))

    assert await make_gemini(handler).complete("hi") == "ok"


# --- Bedrock -------------------------------------------------------------------

class FakeBedrock:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def bedrock_response(text, stop="end_turn"):
    return {
        "output": {"message": {"content": [{"text": text}]}},
        "stopReason": stop,
    }


class FakeClientError(Exception):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.response = {
            "Error": {"Code": code},
            "ResponseMetadata": {"HTTPStatusCode": status},
        }


async def test_bedrock_happy_path():
    fake = FakeBedrock([bedrock_response("hi there")])
    client = BedrockClient(model="anthropic.claude", max_tokens=64, retry_delay=0, client=fake)
    assert await client.complete("q") == "hi there"
    call = fake.calls[0]
    assert call["modelId"] == "anthropic.claude"
    assert call["inferenceConfig"] == {"maxTokens": 64, "temperature": 0}
    assert call["messages"][0]["content"] == [{"text": "q"}]


async def test_bedrock_max_tokens_is_budget_exceeded():
    fake = FakeBedrock([bedrock_response("t", stop="max_tokens")])
    client = BedrockClient(model="m", retry_delay=0, client=fake)
    with pytest.raises(BudgetExceededError):
        await client.complete("q")


async def test_bedrock_throttle_retried_once():
    fake = FakeBedrock([FakeClientError("ThrottlingException", 429), bedrock_response("ok")])
    client = BedrockClient(model="m", retry_delay=0, client=fake)
    assert await client.complete("q") == "ok"
    assert len(fake.calls) == 2


async def test_bedrock_5xx_twice_is_llm_unavailable():
    fake = FakeBedrock(
        [FakeClientError("InternalServerException", 500), FakeClientError("InternalServerException", 500)]
    )
    client = BedrockClient(model="m", retry_delay=0, client=fake)
    with pytest.raises(LLMUnavailableError):
        await client.complete("q")


async def test_bedrock_validation_error_fails_immediately():
    fake = FakeBedrock([FakeClientError("ValidationException", 400)])
    client = BedrockClient(model="m", retry_delay=0, client=fake)
    with pytest.raises(LLMUnavailableError):
        await client.complete("q")
    assert len(fake.calls) == 1


# --- make_llm_client -----------------------------------------------------------

def config(**overrides):
    base = dict(
        llm_provider="gemini",
        llm_model="m",
        llm_api_key="k",
        llm_base_url=None,
        llm_max_tokens=4096,
        aws_region=None,
        plan_max_steps=8,
        plan_max_fanout=10,
        planner_max_matches=20,
        rfff_seed_database_url="x",
        contracts_dir=None,
    )
    base.update(overrides)
    return AgentConfig(**base)


def test_make_gemini_client():
    assert isinstance(make_llm_client(config()), GeminiClient)


def test_make_gemini_requires_api_key():
    with pytest.raises(LLMConfigError):
        make_llm_client(config(llm_api_key=""))


def test_make_requires_model():
    with pytest.raises(LLMConfigError):
        make_llm_client(config(llm_model=""))


def test_make_unknown_provider():
    with pytest.raises(LLMConfigError):
        make_llm_client(config(llm_provider="openai"))


def test_make_bedrock_client(monkeypatch):
    import agent.llm as llm_module

    created = {}

    class Sentinel:
        def __init__(self, **kw):
            created.update(kw)

    monkeypatch.setattr(llm_module, "BedrockClient", Sentinel)
    client = make_llm_client(config(llm_provider="bedrock", aws_region="us-gov-west-1"))
    assert isinstance(client, Sentinel)
    assert created["model"] == "m"
    assert created["region"] == "us-gov-west-1"

"""LLMClient: the ONLY LLM access point (invariant 8). One interface, two
providers selected by LLM_PROVIDER:

- gemini: simple API-key HTTP API over httpx (local testing / demos)
- bedrock: Bedrock Converse API via boto3, credentials inherited from the
  container's IAM role (prod); boto3 is imported lazily so gemini-only
  deployments never need it

Failure mapping (integration-api spec): output hitting the LLM_MAX_TOKENS
cap -> BudgetExceededError; endpoint unreachable or erroring after ONE
retry -> LLMUnavailableError.
"""
import asyncio
from typing import Protocol

import httpx

from agent.config import AgentConfig
from agent.errors import BudgetExceededError, LLMConfigError, LLMUnavailableError

_GEMINI_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"


class LLMClient(Protocol):
    async def complete(self, prompt: str, *, json_mode: bool = False) -> str: ...


class GeminiClient:
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str | None = None,
        max_tokens: int = 4096,
        timeout: float = 60.0,
        retry_delay: float = 0.5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._base_url = (base_url or _GEMINI_DEFAULT_BASE_URL).rstrip("/")
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._retry_delay = retry_delay
        self._transport = transport

    async def complete(self, prompt: str, *, json_mode: bool = False) -> str:
        generation_config: dict = {
            "maxOutputTokens": self._max_tokens,
            "temperature": 0,
        }
        if json_mode:
            generation_config["responseMimeType"] = "application/json"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }
        url = f"{self._base_url}/v1beta/models/{self._model}:generateContent"
        last_error = "unknown error"
        for attempt in range(2):
            if attempt:
                await asyncio.sleep(self._retry_delay)
            try:
                async with httpx.AsyncClient(
                    transport=self._transport, timeout=self._timeout
                ) as client:
                    response = await client.post(
                        url, json=body, headers={"x-goog-api-key": self._api_key}
                    )
            except httpx.HTTPError as exc:
                last_error = f"transport error: {exc}"
                continue
            if response.status_code >= 500 or response.status_code == 429:
                last_error = f"HTTP {response.status_code}"
                continue
            if response.status_code >= 400:
                raise LLMUnavailableError(
                    f"LLM endpoint rejected the request: HTTP {response.status_code}"
                )
            try:
                return self._parse(response.json())
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                last_error = f"malformed response body: {exc!r}"
                continue
        raise LLMUnavailableError(f"LLM endpoint failed after retry: {last_error}")

    def _parse(self, payload: dict) -> str:
        candidate = payload["candidates"][0]
        if candidate.get("finishReason") == "MAX_TOKENS":
            raise BudgetExceededError(
                "LLM output exceeded the LLM_MAX_TOKENS per-call cap"
            )
        return "".join(part["text"] for part in candidate["content"]["parts"])


_BEDROCK_RETRYABLE_CODES = {
    "ThrottlingException",
    "ServiceUnavailableException",
    "InternalServerException",
    "ModelTimeoutException",
    "ModelNotReadyException",
}
_BEDROCK_RETRYABLE_EXC_NAMES = {
    "EndpointConnectionError",
    "ConnectTimeoutError",
    "ReadTimeoutError",
    "ConnectionClosedError",
}


class BedrockClient:
    def __init__(
        self,
        *,
        model: str,
        region: str | None = None,
        max_tokens: int = 4096,
        retry_delay: float = 0.5,
        client=None,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._retry_delay = retry_delay
        if client is None:
            import boto3  # lazy: only bedrock deployments need it installed

            client = boto3.client("bedrock-runtime", region_name=region)
        self._client = client

    async def complete(self, prompt: str, *, json_mode: bool = False) -> str:
        # The Converse API has no JSON output mode; the prompt itself demands
        # a JSON-only response when json_mode is requested.
        del json_mode
        last_error = "unknown error"
        for attempt in range(2):
            if attempt:
                await asyncio.sleep(self._retry_delay)
            try:
                response = await asyncio.to_thread(
                    self._client.converse,
                    modelId=self._model,
                    messages=[{"role": "user", "content": [{"text": prompt}]}],
                    inferenceConfig={"maxTokens": self._max_tokens, "temperature": 0},
                )
            except Exception as exc:  # botocore exceptions, classified below
                if _bedrock_retryable(exc):
                    last_error = f"{type(exc).__name__}: {exc}"
                    continue
                raise LLMUnavailableError(
                    f"LLM endpoint rejected the request: {type(exc).__name__}: {exc}"
                )
            if response.get("stopReason") == "max_tokens":
                raise BudgetExceededError(
                    "LLM output exceeded the LLM_MAX_TOKENS per-call cap"
                )
            try:
                content = response["output"]["message"]["content"]
                return "".join(part["text"] for part in content if "text" in part)
            except (KeyError, TypeError) as exc:
                last_error = f"malformed response body: {exc!r}"
                continue
        raise LLMUnavailableError(f"LLM endpoint failed after retry: {last_error}")


def _bedrock_retryable(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code", "")
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        return code in _BEDROCK_RETRYABLE_CODES or status >= 500
    return type(exc).__name__ in _BEDROCK_RETRYABLE_EXC_NAMES


def make_llm_client(config: AgentConfig) -> LLMClient:
    if not config.llm_model:
        raise LLMConfigError("LLM_MODEL is required")
    if config.llm_provider == "gemini":
        if not config.llm_api_key:
            raise LLMConfigError("LLM_API_KEY is required for the gemini provider")
        return GeminiClient(
            model=config.llm_model,
            api_key=config.llm_api_key,
            base_url=config.llm_base_url,
            max_tokens=config.llm_max_tokens,
        )
    if config.llm_provider == "bedrock":
        return BedrockClient(
            model=config.llm_model,
            region=config.aws_region,
            max_tokens=config.llm_max_tokens,
        )
    raise LLMConfigError(f"unknown LLM_PROVIDER {config.llm_provider!r}")

"""LLM provider abstraction.

Agents never depend on an LLM for correctness. Each agent has a deterministic path; when a real
provider is configured, the LLM *enriches* that output and every claim it makes is grounded
against the source text before it is accepted. A failed or refused call degrades gracefully to
the deterministic result and is recorded, never silently swallowed.

Providers:
- mock:      no network. Returns no structured value (or a scripted one in tests).
- anthropic: Claude via structured outputs (`messages.parse` with a Pydantic schema).
- gemini:    Google Gemini via `google-genai` with a JSON schema (`response_json_schema`).
"""

from __future__ import annotations

import contextvars
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.config import get_settings
from app.core.logging import get_logger, log_ctx
from app.services.untrusted import UNTRUSTED_SYSTEM_RULES

logger = get_logger(__name__)
T = TypeVar("T", bound=BaseModel)


@dataclass
class LLMResult(Generic[T]):
    value: T | None
    provider: str
    model: str | None = None
    degraded: bool = False
    error: str | None = None
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class UsageMeter:
    calls: int = 0
    failures: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    by_task: dict[str, int] = field(default_factory=dict)

    def record(self, task: str, r: LLMResult) -> None:
        self.calls += 1
        self.failures += int(r.degraded)
        self.input_tokens += r.input_tokens
        self.output_tokens += r.output_tokens
        self.latency_ms += r.latency_ms
        self.by_task[task] = self.by_task.get(task, 0) + 1

    def as_dict(self) -> dict:
        return {
            "calls": self.calls,
            "failures": self.failures,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms,
            "by_task": self.by_task,
        }


# Workflows set a meter for the duration of a run so usage can be attributed to it.
current_meter: contextvars.ContextVar[UsageMeter | None] = contextvars.ContextVar("llm_meter", default=None)


class LLMProvider(Protocol):
    name: str

    def structured(
        self, *, task: str, system: str, content: str, schema: type[T], max_tokens: int = 4000
    ) -> LLMResult[T]: ...


def _meter(task: str, r: LLMResult) -> LLMResult:
    m = current_meter.get()
    if m is not None and r.provider != "mock":
        m.record(task, r)
    return r


class MockLLM:
    """Deterministic provider. `scripted` maps task name -> value or callable(content) -> value."""

    name = "mock"

    def __init__(self, scripted: dict[str, BaseModel | Callable[[str], BaseModel | None]] | None = None) -> None:
        self.scripted = scripted or {}
        self.calls: list[str] = []

    def structured(
        self, *, task: str, system: str, content: str, schema: type[T], max_tokens: int = 4000
    ) -> LLMResult[T]:
        self.calls.append(task)
        item = self.scripted.get(task)
        if item is None:
            return LLMResult(value=None, provider=self.name)
        value = item(content) if callable(item) else item
        if value is not None and not isinstance(value, schema):
            value = schema.model_validate(value)
        return LLMResult(value=value, provider=self.name, model="scripted")


class AnthropicLLM:
    name = "anthropic"

    def __init__(self, api_key: str, model: str, timeout: float, max_retries: int, use_fallbacks: bool = True) -> None:
        import anthropic

        self._anthropic = anthropic
        # The SDK retries connection errors, 408/409/429 and 5xx with exponential backoff.
        self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=max_retries)
        self.model = model
        self.use_fallbacks = use_fallbacks

    def structured(
        self, *, task: str, system: str, content: str, schema: type[T], max_tokens: int = 4000
    ) -> LLMResult[T]:
        a = self._anthropic
        started = time.monotonic()
        kwargs: dict = {}
        if self.use_fallbacks:
            # On a safety-classifier refusal the API re-runs the request on a fallback model.
            kwargs = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        try:
            response = self.client.beta.messages.parse(
                model=self.model,
                max_tokens=max_tokens,
                system=f"{system}\n\n{UNTRUSTED_SYSTEM_RULES}",
                messages=[{"role": "user", "content": content}],
                output_format=schema,
                output_config={"effort": "low"},
                **kwargs,
            )
        except a.RateLimitError as exc:
            return self._fail(task, started, f"rate_limited: {exc.message}")
        except a.APITimeoutError:
            return self._fail(task, started, "timeout")
        except a.APIStatusError as exc:
            return self._fail(task, started, f"api_error_{exc.status_code}")
        except a.APIConnectionError:
            return self._fail(task, started, "connection_error")
        except ValidationError as exc:
            return self._fail(task, started, f"schema_validation_failed: {exc.error_count()} errors")

        latency = int((time.monotonic() - started) * 1000)
        usage = getattr(response, "usage", None)
        tokens = {
            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        }
        if response.stop_reason == "refusal":
            return _meter(task, LLMResult(None, self.name, self.model, True, "refusal", latency, **tokens))
        if response.stop_reason == "max_tokens":
            return _meter(task, LLMResult(None, self.name, self.model, True, "truncated", latency, **tokens))
        value = getattr(response, "parsed_output", None)
        if value is None:
            return _meter(task, LLMResult(None, self.name, self.model, True, "no_parsed_output", latency, **tokens))
        return _meter(task, LLMResult(value, self.name, self.model, False, None, latency, **tokens))

    def _fail(self, task: str, started: float, error: str) -> LLMResult:
        logger.warning("LLM call degraded", extra=log_ctx(task=task, error=error))
        return _meter(
            task, LLMResult(None, self.name, self.model, True, error, int((time.monotonic() - started) * 1000))
        )


class GeminiLLM:
    """Google Gemini via the official `google-genai` SDK, using JSON-schema constrained output."""

    name = "gemini"
    BLOCKED = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "IMAGE_SAFETY"}

    # Quota exhausted / overloaded: move to the next model right away instead of retrying the same one.
    SWITCH_MODEL = {429, 503, 504}

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float,
        max_retries: int,
        thinking_level: str = "",
        fallback_models: list[str] | None = None,
    ) -> None:
        from google import genai
        from google.genai import types

        self._types = types
        self.models = list(dict.fromkeys([model, *(fallback_models or [])]))
        self.model = model
        self.timeout = timeout
        self.thinking = types.ThinkingConfig(thinking_level=thinking_level.upper()) if thinking_level else None
        # The SDK retries other transient statuses with backoff; timeout is in milliseconds.
        self.client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=int(timeout * 1000),
                retry_options=types.HttpRetryOptions(attempts=max_retries + 1, http_status_codes=[408, 500, 502]),
            ),
        )

    def structured(
        self, *, task: str, system: str, content: str, schema: type[T], max_tokens: int = 4000
    ) -> LLMResult[T]:
        import httpx
        from google.genai import errors

        started = time.monotonic()
        config = self._types.GenerateContentConfig(
            system_instruction=f"{system}\n\n{UNTRUSTED_SYSTEM_RULES}",
            response_mime_type="application/json",
            response_json_schema=schema.model_json_schema(),
            # Thinking tokens count against the output budget on 2.5+ models; leave headroom.
            max_output_tokens=max(max_tokens, 8192),
            temperature=0.0,
            thinking_config=self.thinking,
        )
        response = None
        reasons: list[str] = []
        for i, model in enumerate(self.models):
            if i and time.monotonic() - started > self.timeout:
                reasons.append("time_budget_exhausted")
                break  # never let fallbacks stretch one request beyond the configured timeout
            try:
                response = self.client.models.generate_content(model=model, contents=content, config=config)
                self.model = model
                break
            except errors.APIError as exc:
                reasons.append("rate_limited" if exc.code == 429 else f"api_error_{exc.code}")
                if exc.code not in self.SWITCH_MODEL:
                    break
                logger.info("Gemini model unavailable, trying fallback", extra=log_ctx(model=model, code=exc.code))
            except httpx.TimeoutException:
                reasons.append("timeout")
            except httpx.HTTPError:
                reasons.append("connection_error")
                break
        if response is None:
            return self._fail(task, started, " > ".join(reasons) if len(reasons) > 1 else reasons[0])

        latency = int((time.monotonic() - started) * 1000)
        usage = response.usage_metadata
        tokens = {
            "input_tokens": (usage.prompt_token_count or 0) if usage else 0,
            "output_tokens": ((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)) if usage else 0,
        }
        feedback = response.prompt_feedback
        if feedback is not None and feedback.block_reason:
            return _meter(task, LLMResult(None, self.name, self.model, True, "refusal", latency, **tokens))
        finish = response.candidates[0].finish_reason if response.candidates else None
        finish_name = getattr(finish, "name", str(finish)) if finish is not None else None
        if finish_name in self.BLOCKED:
            return _meter(task, LLMResult(None, self.name, self.model, True, "refusal", latency, **tokens))
        if finish_name == "MAX_TOKENS":
            return _meter(task, LLMResult(None, self.name, self.model, True, "truncated", latency, **tokens))
        try:
            value = schema.model_validate_json(response.text or "")
        except ValidationError as exc:
            # Pydantic reports unparseable JSON as a validation error of type "json_invalid".
            if any(e["type"] == "json_invalid" for e in exc.errors()):
                reason = "invalid_json"
            else:
                reason = f"schema_validation_failed: {exc.error_count()} errors"
            return _meter(task, LLMResult(None, self.name, self.model, True, reason, latency, **tokens))
        return _meter(task, LLMResult(value, self.name, self.model, False, None, latency, **tokens))

    def _fail(self, task: str, started: float, error: str) -> LLMResult:
        logger.warning("LLM call degraded", extra=log_ctx(task=task, provider=self.name, error=error))
        return _meter(
            task, LLMResult(None, self.name, self.model, True, error, int((time.monotonic() - started) * 1000))
        )


_provider: LLMProvider | None = None


def get_llm() -> LLMProvider:
    global _provider
    if _provider is None:
        s = get_settings()
        provider, _ = s.active_llm()
        if provider == "anthropic":
            _provider = AnthropicLLM(
                s.anthropic_api_key, s.llm_model, s.llm_timeout_seconds, s.llm_max_retries, s.llm_use_fallbacks
            )
        elif provider == "gemini":
            _provider = GeminiLLM(
                s.gemini_api_key,
                s.gemini_model,
                s.llm_timeout_seconds,
                s.llm_max_retries,
                s.gemini_thinking_level,
                s.gemini_fallback_models,
            )
        else:
            if s.llm_provider != "mock":
                key = f"{s.llm_provider.upper()}_API_KEY"
                logger.warning(f"LLM_PROVIDER={s.llm_provider} but {key} is not set; using mock provider")
            _provider = MockLLM()
    return _provider


def set_llm(provider: LLMProvider | None) -> None:
    """Override the provider (tests) or reset to settings-derived (None)."""
    global _provider
    _provider = provider

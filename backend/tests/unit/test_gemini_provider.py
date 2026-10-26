"""Gemini provider behavior with the SDK's network call stubbed (no API key or network needed)."""

from types import SimpleNamespace

import pytest
from google.genai import errors

from app.core.config import get_settings
from app.schemas.agents import LLMRubricJudgement
from app.services import llm as llm_mod
from app.services.llm import GeminiLLM, MockLLM, UsageMeter, current_meter, get_llm, set_llm

GOOD = '{"criteria": [{"id": "idempotency_key", "met": 1.0, "quote": "use an idempotency key"}], "feedback": "ok"}'


def response(text=GOOD, finish="STOP", block=None):
    usage = SimpleNamespace(prompt_token_count=120, candidates_token_count=30, thoughts_token_count=10)
    cand = SimpleNamespace(finish_reason=SimpleNamespace(name=finish))
    return SimpleNamespace(
        text=text,
        usage_metadata=usage,
        candidates=[cand],
        prompt_feedback=SimpleNamespace(block_reason=block) if block else None,
    )


def provider(result):
    p = GeminiLLM(api_key="test-key", model="gemini-test", timeout=5, max_retries=1)
    calls = []

    def generate_content(**kwargs):
        calls.append(kwargs)
        if isinstance(result, Exception):
            raise result
        return result

    p.client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    return p, calls


def run(p):
    return p.structured(
        task="interview_grading",
        system="Grade.",
        content="<untrusted_answer>x</untrusted_answer>",
        schema=LLMRubricJudgement,
    )


def test_success_is_parsed_validated_and_metered():
    p, calls = provider(response())
    meter = UsageMeter()
    token = current_meter.set(meter)
    try:
        r = run(p)
    finally:
        current_meter.reset(token)
    assert not r.degraded and isinstance(r.value, LLMRubricJudgement)
    assert r.value.criteria[0].quote == "use an idempotency key"
    assert (r.input_tokens, r.output_tokens) == (120, 40)  # thinking tokens are counted as output
    cfg = calls[0]["config"]
    assert calls[0]["model"] == "gemini-test" and cfg.response_mime_type == "application/json"
    assert cfg.response_json_schema["title"] == "LLMRubricJudgement"
    assert "untrusted" in cfg.system_instruction  # security rules always appended
    assert meter.calls == 1 and meter.by_task == {"interview_grading": 1}


@pytest.mark.parametrize(
    "resp,reason",
    [
        (response(block="SAFETY"), "refusal"),
        (response(finish="SAFETY"), "refusal"),
        (response(finish="MAX_TOKENS"), "truncated"),
        (response(text="not json"), "invalid_json"),
        (response(text='{"criteria": [{"id": "x", "met": 7}], "feedback": "f"}'), "schema_validation_failed: 1 errors"),
    ],
)
def test_unusable_responses_degrade_with_reason(resp, reason):
    r = run(provider(resp)[0])
    assert r.value is None and r.degraded and r.error == reason


def test_api_errors_degrade_instead_of_raising():
    r = run(provider(errors.ClientError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}}))[0])
    assert r.degraded and r.error == "rate_limited"
    r = run(provider(errors.ServerError(503, {"error": {"message": "unavailable"}}))[0])
    assert r.error == "api_error_503"


def test_factory_selects_gemini_only_with_a_key(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "llm_provider", "gemini")
    monkeypatch.setattr(s, "gemini_api_key", None)
    set_llm(None)
    assert isinstance(get_llm(), MockLLM) and s.active_llm() == ("mock", None)
    monkeypatch.setattr(s, "gemini_api_key", "test-key")
    set_llm(None)
    p = get_llm()
    assert isinstance(p, GeminiLLM) and s.active_llm() == ("gemini", s.gemini_model)
    set_llm(None)


def test_meta_reports_active_provider(client, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "llm_provider", "gemini")
    monkeypatch.setattr(s, "gemini_api_key", "test-key")
    assert client.get("/api/meta").json()["llm"] == {"provider": "gemini", "model": s.gemini_model}
    monkeypatch.setattr(s, "gemini_api_key", None)
    assert client.get("/api/meta").json()["llm"] == {"provider": "mock", "model": None}


def test_grounding_still_applies_to_gemini_output(monkeypatch):
    """An ungrounded quote from Gemini earns no credit: the same check as for any provider."""
    from app.agents.interview_agent import InterviewCoachAgent, bank_item

    fake = response(
        '{"criteria": [{"id": "idempotency_key", "met": 1.0, "quote": "I use idempotency keys everywhere"}],'
        ' "feedback": "great"}'
    )
    p, _ = provider(fake)
    q = bank_item("be-idempotency")
    answer = "I would retry the request later and it should mostly be fine in practice for our users."
    assert InterviewCoachAgent(p).evaluate(q, answer).score == InterviewCoachAgent(MockLLM()).evaluate(q, answer).score
    assert llm_mod.GeminiLLM is GeminiLLM


def test_rate_limited_model_falls_back_to_next_model():
    p = GeminiLLM(api_key="k", model="primary", timeout=5, max_retries=0, fallback_models=["second", "third"])
    seen = []

    def generate_content(model, **kwargs):
        seen.append(model)
        if model == "primary":
            raise errors.ClientError(429, {"error": {"message": "quota"}})
        if model == "second":
            raise errors.ServerError(503, {"error": {"message": "overloaded"}})
        return response()

    p.client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    r = run(p)
    assert seen == ["primary", "second", "third"] and r.value is not None and r.model == "third"


def test_non_transient_errors_do_not_try_other_models():
    p = GeminiLLM(api_key="k", model="primary", timeout=5, max_retries=0, fallback_models=["second"])
    seen = []

    def generate_content(model, **kwargs):
        seen.append(model)
        raise errors.ClientError(400, {"error": {"message": "bad request"}})

    p.client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    r = run(p)
    assert seen == ["primary"] and r.error == "api_error_400"


def test_all_models_unavailable_reports_each_reason():
    p = GeminiLLM(api_key="k", model="a", timeout=5, max_retries=0, fallback_models=["b"])

    def generate_content(model, **kwargs):
        raise errors.ClientError(429, {"error": {"message": "quota"}})

    p.client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    assert run(p).error == "rate_limited > rate_limited"

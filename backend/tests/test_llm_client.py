"""OpenAI-compatible LLM clients and the provider fallback chain (httpx.MockTransport, no network)."""
import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from services import llm_client
from services.llm_client import (PROVIDERS, AgentUnavailable, FallbackClient, GroqClient, OpenAICompatClient,
                                 ToolCallRejected, build_llm_client)

SECRET = "sk-test-secret-123"


@pytest.fixture(autouse=True)
def fresh_cooldowns():
    llm_client.clear_cooldowns()
    yield
    llm_client.clear_cooldowns()


def ok_body(content=None, tool_calls=None, usage=None):
    body = {"choices": [{"message": {"role": "assistant", "content": content, "tool_calls": tool_calls}}]}
    return {**body, "usage": usage} if usage else body


def transport(handler, seen=None):
    def wrapped(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return handler(request)
    return httpx.MockTransport(wrapped)


def respond(status, body=None, headers=None):
    return lambda _request: httpx.Response(status, json=body if body is not None else {}, headers=headers)


def client_for(name, handler, seen=None, key=SECRET):
    return OpenAICompatClient(PROVIDERS[name], key, transport=transport(handler, seen))


def test_posts_openai_payload_with_bearer_key_only():
    seen = []
    calls = [{"id": "t1", "type": "function", "function": {"name": "search_players", "arguments": '{"query":"x"}'}}]
    client = client_for("groq", respond(200, ok_body(tool_calls=calls, usage={"prompt_tokens": 12,
                                                                                "completion_tokens": 3})), seen)
    result = client.complete([{"role": "user", "content": "hi"}], [{"type": "function"}])

    assert result == {"content": None, "usage": {"prompt_tokens": 12, "completion_tokens": 3},
                      "tool_calls": [{"id": "t1", "name": "search_players", "arguments": '{"query":"x"}'}]}
    request = seen[0]
    assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {SECRET}"
    assert SECRET not in str(request.url)
    sent = json.loads(request.content)
    assert sent["model"] == "openai/gpt-oss-120b" and sent["temperature"] == 0.2
    assert sent["tool_choice"] == "auto" and sent["reasoning_effort"] == "low"


def test_no_tools_means_no_tool_choice_and_no_reasoning_effort_for_other_models():
    seen = []
    client_for("gemini", respond(200, ok_body("Answer.")), seen).complete([], None)
    sent = json.loads(seen[0].content)
    assert "tools" not in sent and "tool_choice" not in sent and "reasoning_effort" not in sent
    assert str(seen[0].url).startswith("https://generativelanguage.googleapis.com/v1beta/openai/")


def test_ollama_needs_no_key_and_skips_tool_choice():
    seen = []
    client_for("ollama", respond(200, ok_body("ok")), seen, key=None).complete([], [{"type": "function"}])
    assert "authorization" not in seen[0].headers
    sent = json.loads(seen[0].content)
    assert sent["tools"] == [{"type": "function"}] and "tool_choice" not in sent


def test_extra_tool_call_fields_round_trip_only_for_providers_that_keep_them():
    signed = {"id": "c1", "type": "function", "function": {"name": "f", "arguments": {"a": 1}},
              "extra_content": {"google": {"thought_signature": "sig"}}}
    reply = client_for("gemini", respond(200, ok_body(tool_calls=[signed]))).complete([], None)
    call = reply["tool_calls"][0]
    assert call["arguments"] == '{"a": 1}'                      # objects become JSON strings
    assert call["extra"] == {"extra_content": {"google": {"thought_signature": "sig"}}}

    history = [{"role": "assistant", "content": "", "tool_calls": [{**signed, "function": {"name": "f",
                                                                                         "arguments": "{}"}}]}]
    for name, keeps in (("gemini", True), ("groq", False)):
        seen = []
        client_for(name, respond(200, ok_body("ok")), seen).complete(history, None)
        sent_call = json.loads(seen[0].content)["messages"][0]["tool_calls"][0]
        assert ("extra_content" in sent_call) is keeps
    assert "extra_content" in history[0]["tool_calls"][0]       # caller's messages are not mutated


def test_429_is_rate_limit_with_retry_after():
    client = client_for("groq", respond(429, {"error": {"code": "rate_limit_exceeded", "message": "slow down"}},
                                        {"retry-after": "17"}))
    with pytest.raises(AgentUnavailable) as info:
        client.complete([], None)
    assert info.value.kind == "rate_limit" and info.value.retry_after == 17
    assert "17 seconds" in str(info.value)


def test_429_without_header_uses_default_wait():
    with pytest.raises(AgentUnavailable) as info:
        client_for("gemini", respond(429, [{"error": {"code": 429}}])).complete([], None)
    assert info.value.retry_after == llm_client.DEFAULT_RETRY_AFTER


@pytest.mark.parametrize("status, body", [
    (413, {"error": {"message": "Request too large for model"}}),
    (400, {"error": {"message": "Request too large for model `x`: tokens per minute (TPM): Limit 8000"}}),
])
def test_request_too_large_is_not_a_wait(status, body):
    with pytest.raises(AgentUnavailable) as info:
        client_for("groq", respond(status, body)).complete([], None)
    assert info.value.kind == "too_large" and info.value.retry_after is None
    assert "wait" not in str(info.value).lower() and "narrower" in str(info.value)


def test_schema_violating_tool_call_is_rejected():
    body = {"error": {"code": "tool_use_failed", "message": "parameter 'limit' must be <= 50"}}
    with pytest.raises(ToolCallRejected, match="limit"):
        client_for("groq", respond(400, body)).complete([], None)


@pytest.mark.parametrize("handler", [
    respond(500, {"error": {"message": f"boom key={SECRET} https://x?key={SECRET}"}}),
    respond(401, {"error": {"message": f"bad key {SECRET}"}}),
    respond(200, {"unexpected": True}),
])
def test_failures_never_leak_bodies_or_keys(handler, caplog):
    with pytest.raises(AgentUnavailable) as info:
        client_for("groq", handler).complete([], None)
    assert info.value.kind == "provider"
    assert SECRET not in str(info.value) and SECRET not in caplog.text


def test_connection_errors_carry_the_type_only():
    def down(request):
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)
    with pytest.raises(AgentUnavailable, match="ConnectError") as info:
        client_for("groq", down).complete([], None)
    assert "groq.com" not in str(info.value)


def test_groq_client_alias_uses_the_chosen_model():
    seen = []
    client = GroqClient("key", "openai/gpt-oss-20b", transport=transport(respond(200, ok_body("hi")), seen))
    assert client.complete([], None)["content"] == "hi"
    assert json.loads(seen[0].content)["model"] == "openai/gpt-oss-20b"
    assert client.profile.name == "groq" and client.last_provider == "groq"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_fallback_moves_on_after_rate_limit_and_cools_down():
    groq_seen, gemini_seen = [], []
    groq = client_for("groq", respond(429, {}, {"retry-after": "30"}), groq_seen)
    gemini = client_for("gemini", respond(200, ok_body("from gemini")), gemini_seen)
    clock = Clock()
    chain = FallbackClient([groq, gemini], clock=clock)

    assert chain.complete([], None)["content"] == "from gemini"
    assert chain.last_provider == "gemini" and chain.profile.max_tool_chars == 24_000
    chain.complete([], None)
    assert len(groq_seen) == 1 and len(gemini_seen) == 2          # groq skipped while cooling down

    clock.now += 31
    FallbackClient([groq, gemini], clock=clock).complete([], None)  # a new request tries groq again
    assert len(groq_seen) == 2


def test_fallback_skips_down_provider():
    chain = FallbackClient([client_for("groq", respond(503)), client_for("openrouter", respond(200, ok_body("ok")))])
    assert chain.complete([], None)["content"] == "ok" and chain.last_provider == "openrouter"


@pytest.mark.parametrize("handler, error", [
    (respond(413, {}), AgentUnavailable),
    (respond(400, {"error": {"code": "tool_use_failed", "message": "bad"}}), ToolCallRejected),
])
def test_fallback_does_not_hide_too_large_or_rejected_calls(handler, error):
    backup_seen = []
    chain = FallbackClient([client_for("groq", handler), client_for("gemini", respond(200, ok_body("x")),
                                                                    backup_seen)])
    with pytest.raises(error):
        chain.complete([], None)
    assert backup_seen == []


def test_fallback_all_limited_reports_shortest_wait():
    clock = Clock()
    chain = FallbackClient([client_for("groq", respond(429, {}, {"retry-after": "40"})),
                            client_for("gemini", respond(429, {}, {"retry-after": "90"}))], clock=clock)
    with pytest.raises(AgentUnavailable) as info:
        chain.complete([], None)
    assert info.value.kind == "rate_limit" and info.value.retry_after == 40
    clock.now += 10
    with pytest.raises(AgentUnavailable) as info:                  # nothing is tried while all cool down
        chain.complete([], None)
    assert info.value.retry_after == 30


def test_fallback_all_down_keeps_provider_kind():
    chain = FallbackClient([client_for("groq", respond(500)), client_for("gemini", respond(502))])
    with pytest.raises(AgentUnavailable, match="HTTP 502") as info:
        chain.complete([], None)
    assert info.value.kind == "provider"


def settings(**overrides):
    base = {"llm_chain": "groq", "groq_api_key": None, "groq_model": "openai/gpt-oss-120b", "gemini_api_key": None,
            "gemini_model": None, "openrouter_api_key": None, "openrouter_model": None, "ollama_model": None,
            "ollama_base_url": None}
    return SimpleNamespace(**{**base, **overrides})


def test_build_returns_none_without_keys():
    assert build_llm_client(settings()) is None
    assert build_llm_client(settings(llm_chain="gemini,openrouter")) is None


def test_build_default_is_a_single_groq_client():
    client = build_llm_client(settings(groq_api_key="k", groq_model="openai/gpt-oss-20b"))
    assert isinstance(client, OpenAICompatClient)
    assert client.profile == replace(PROVIDERS["groq"], model="openai/gpt-oss-20b")


def test_build_chain_skips_unkeyed_and_unknown_providers():
    client = build_llm_client(settings(llm_chain=" Groq, nope ,gemini, openrouter,ollama", groq_api_key="g",
                                       openrouter_api_key="o", ollama_base_url="http://127.0.0.1:9999/v1"))
    assert isinstance(client, FallbackClient)
    names = [c.profile.name for c in client._clients]
    assert names == ["groq", "openrouter", "ollama"]
    assert client._clients[2].profile.base_url == "http://127.0.0.1:9999/v1"


def test_real_settings_default_to_groq(monkeypatch):
    from config import get_settings

    monkeypatch.setenv("GROQ_API_KEY", "k")
    get_settings.cache_clear()
    client = build_llm_client(get_settings())
    assert get_settings().llm_chain == "groq" and client.profile.name == "groq"

"""Chat-completion clients for the AI assistant: any OpenAI-compatible provider, with fallback.

Groq, Google Gemini, OpenRouter and a local Ollama all speak the same `/chat/completions`
protocol, so one small httpx client covers them (no provider SDKs). `LLM_CHAIN` in backend/.env
lists providers in order ("groq" by default); when one hits its free-tier limit or is down,
the next one answers. Free tiers differ a lot in how much text a request may carry, so each
provider's profile sets the tool-result and input budgets the agent loop uses.

API keys only ever travel in the Authorization header, and errors carry status codes and
exception types, never response bodies or URLs.
"""
import json
import logging
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal

import httpx

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 20            # one chat can make several calls; don't hold a worker for minutes
TEMPERATURE = 0.2
DEFAULT_RETRY_AFTER = 60        # seconds, when a provider doesn't say
MAX_RETRY_AFTER = 24 * 3600     # a daily cap resets within a day
PROVIDER_DOWN_COOLDOWN = 30     # skip a failing provider this long before trying it again
TOOL_CALL_KEYS = ("id", "type", "function")
CHARS_PER_TOKEN = 4
# A request estimated this far over a provider's input budget is skipped without a call. The
# budgets sit below the real limits, so a small overshoot is still worth sending.
SKIP_OVER_BUDGET = 1.5
TOO_LARGE_MESSAGE = ("That question needed more data than the AI provider's free tier accepts in one request. "
                     "Ask a narrower question (one player, position or league).")
# The provider's own error text echoes model output, so only a tool name (an identifier) is kept.
NOT_TOOL_NAMES = frozenset({"call", "calls", "choice", "use", "validation", "which", "that", "in", "with"})
TOOL_NAME_IN_ERROR = re.compile(r"\btool\s+['\"`]?([A-Za-z_][A-Za-z0-9_]{0,63})\b")

# Gemini 3 rejects (HTTP 400) earlier tool calls without a thought signature, e.g. Groq's calls when a
# question moves to Gemini mid-way. Google documents this value for calls made outside Gemini.
SIGNATURE_PLACEHOLDER = "skip_thought_signature_validator"
ERROR_CODE = re.compile(r"[A-Za-z_]{1,40}")    # an error's status enum (e.g. UNAVAILABLE) is safe to log
# A provider's next model is tried when one is overloaded, missing or out of its own quota.
MODEL_FAILURE_STATUSES = frozenset({400, 404, 429, 500, 502, 503, 504})

ErrorKind = Literal["rate_limit", "too_large", "provider"]


class AgentUnavailable(RuntimeError):
    """The model can't answer right now. `kind` tells the API which status and advice to give."""

    def __init__(self, message: str, kind: ErrorKind = "provider", retry_after: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after


class ToolCallRejected(RuntimeError):
    """The provider refused the model's own tool call (e.g. arguments outside the schema)."""


@dataclass(frozen=True)
class ProviderProfile:
    name: str
    base_url: str
    key_env: str | None          # None: no key needed (local Ollama)
    model: str
    max_tool_chars: int          # budget for one round of tool results
    max_input_tokens: int        # rough cap for a whole request (chars / 4)
    supports_tool_choice: bool = True
    keeps_extra: bool = False    # echo provider-specific tool-call fields (Gemini's thought signatures)
    # Tried in order when `model` is overloaded or out of its own quota (Gemini's newest Flash often answers
    # 503 "high demand", and each Gemini model has its own small free daily quota).
    fallback_models: tuple[str, ...] = ()


# Free-tier budgets. Groq allows ~8,000 tokens per minute in total, so a request must stay well
# under that; Gemini Flash and the OpenRouter free models accept far longer requests.
PROVIDERS: dict[str, ProviderProfile] = {
    "groq": ProviderProfile("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY",
                            "openai/gpt-oss-120b", max_tool_chars=6_000, max_input_tokens=5_000),
    "gemini": ProviderProfile("gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
                              "GEMINI_API_KEY", "gemini-3.8-flash", max_tool_chars=24_000,
                              max_input_tokens=60_000, keeps_extra=True,
                              fallback_models=("gemini-3.5-flash", "gemini-3.1-flash-lite")),
    "openrouter": ProviderProfile("openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
                                  "google/gemma-4-31b-it:free", max_tool_chars=16_000, max_input_tokens=30_000),
    "ollama": ProviderProfile("ollama", "http://localhost:11434/v1", None, "gpt-oss:20b",
                              max_tool_chars=12_000, max_input_tokens=12_000, supports_tool_choice=False),
}


def _retry_after(response: httpx.Response) -> int:
    try:
        seconds = int(float(response.headers.get("retry-after", "")))
    except ValueError:
        return DEFAULT_RETRY_AFTER
    return min(max(seconds, 1), MAX_RETRY_AFTER)


def _error_body(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    if isinstance(body, list):       # Gemini sometimes wraps the error in a list
        body = body[0] if body else {}
    error = body.get("error", body) if isinstance(body, dict) else None
    return error if isinstance(error, dict) else {}


class _ModelFailed(Exception):
    """This model failed in a way another model of the same provider may not (wraps the error to raise)."""

    def __init__(self, error: AgentUnavailable):
        super().__init__(str(error))
        self.error = error


def _error_code(error: dict) -> str:
    match = ERROR_CODE.fullmatch(str(error.get("status") or error.get("code") or "-"))
    return match.group(0) if match else "-"


def _rejection(message: str) -> str:
    names = [n for n in TOOL_NAME_IN_ERROR.findall(message) if n.lower() not in NOT_TOOL_NAMES]
    target = f"the {names[0]} call" if names else "the tool call"
    return (f"{target[0].upper()}{target[1:]} did not match the tool's schema (wrong argument names, "
            "types, limits or missing required fields)")


def _raise_for_status(name: str, response: httpx.Response) -> None:
    status = response.status_code
    if status < 400:
        return
    error = _error_body(response)
    code = str(error.get("code") or "")
    message = str(error.get("message") or "")
    if status == 400 and code == "tool_use_failed":     # Groq: the model's tool call broke the schema
        log.warning("%s rejected a tool call", name)
        raise ToolCallRejected(_rejection(message))
    if status == 413 or "request too large" in message.lower():
        log.warning("%s: request too large (HTTP %s)", name, status)
        raise AgentUnavailable(TOO_LARGE_MESSAGE, kind="too_large")
    if status == 429 or code == "rate_limit_exceeded":
        wait = _retry_after(response)
        log.warning("%s rate limit (HTTP %s), retry after %ss", name, status, wait)
        raise AgentUnavailable(f"The AI provider's free-tier limit was reached. Try again in about {wait} "
                               "seconds, or ask a narrower question.", kind="rate_limit", retry_after=wait)
    log.warning("%s request failed with HTTP %s (%s)", name, status, _error_code(error))
    raise AgentUnavailable(f"The language model request failed: HTTP {status}")


def _tool_call(raw: dict) -> dict:
    function = raw.get("function") or {}
    arguments = function.get("arguments")
    if not isinstance(arguments, str):      # some servers send an object instead of a JSON string
        arguments = json.dumps(arguments or {})
    call = {"id": str(raw.get("id") or ""), "name": str(function.get("name") or ""), "arguments": arguments}
    extra = {key: value for key, value in raw.items() if key not in TOOL_CALL_KEYS}
    return {**call, "extra": extra} if extra else call


def _usage(data: dict) -> dict | None:
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None
    return {key: int(usage.get(key) or 0) for key in ("prompt_tokens", "completion_tokens")}


def _signed(call: dict) -> dict:
    """A tool call Gemini accepts: one from another provider (no thought signature) gets Google's placeholder."""
    google = (call.get("extra_content") or {}).get("google") or {}
    if google.get("thought_signature"):
        return call
    extra = {**(call.get("extra_content") or {}), "google": {**google, "thought_signature": SIGNATURE_PLACEHOLDER}}
    return {**call, "extra_content": extra}


def _outgoing(messages: Sequence[dict], keeps_extra: bool) -> list[dict]:
    """Tool calls fit the provider (new copies, input untouched): Gemini gets every call signed, the others
    lose provider-specific fields they might reject."""
    def fix(call: dict) -> dict:
        return _signed(call) if keeps_extra else {k: v for k, v in call.items() if k in TOOL_CALL_KEYS}
    return [{**m, "tool_calls": [fix(c) for c in m["tool_calls"]]} if m.get("tool_calls") else m for m in messages]


class OpenAICompatClient:
    """One provider's /chat/completions endpoint. Returns the agent's provider-neutral reply dict."""

    def __init__(self, profile: ProviderProfile, api_key: str | None, *,
                 transport: httpx.BaseTransport | None = None, timeout: float = TIMEOUT_SECONDS):
        self.profile = profile
        # The model that last answered stays first: Gemini's thought signatures belong to one model.
        self._models = tuple(dict.fromkeys((profile.model, *profile.fallback_models)))
        self._model_index = 0
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.Client(base_url=profile.base_url, headers=headers, timeout=timeout, transport=transport)

    @property
    def last_provider(self) -> str:
        return self.profile.name

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "OpenAICompatClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _payload(self, model: str, messages: Sequence[dict], tools: list[dict] | None) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": model, "temperature": TEMPERATURE,
                                   "messages": _outgoing(messages, self.profile.keeps_extra)}
        if "gpt-oss" in model:
            payload["reasoning_effort"] = "low"     # fewer hidden reasoning tokens against the budget
        if tools:
            payload["tools"] = tools
            if self.profile.supports_tool_choice:
                payload["tool_choice"] = "auto"
        return payload

    def complete(self, messages: list[dict], tools: list[dict] | None) -> dict:
        failure: AgentUnavailable | None = None
        for index in range(self._model_index, len(self._models)):
            try:
                reply = self._complete_with(self._models[index], messages, tools, last=index == len(self._models) - 1)
            except _ModelFailed as exc:
                failure = exc.error
                log.warning("%s model %s failed; trying %s", self.profile.name, self._models[index],
                            self._models[index + 1])
                continue
            self._model_index = index
            return reply
        raise failure or AgentUnavailable("The language model request failed.")

    def _complete_with(self, model: str, messages: list[dict], tools: list[dict] | None, last: bool) -> dict:
        """One model's reply; _ModelFailed (unless `last`) when the provider's next model might answer."""
        name = self.profile.name
        try:
            response = self._http.post("chat/completions", json=self._payload(model, messages, tools))
        except httpx.HTTPError as exc:      # timeouts, refused connections, DNS
            log.warning("%s request failed: %s", name, type(exc).__name__)
            error = AgentUnavailable(f"The language model request failed: {type(exc).__name__}")
            if isinstance(exc, httpx.TimeoutException) and not last:
                raise _ModelFailed(error) from exc
            raise error from exc
        try:
            _raise_for_status(name, response)
        except AgentUnavailable as exc:
            if last or response.status_code not in MODEL_FAILURE_STATUSES or exc.kind == "too_large":
                raise
            raise _ModelFailed(exc) from exc
        try:
            data = response.json()
            message = data["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            log.warning("%s returned an unexpected response: %s", name, type(exc).__name__)
            raise AgentUnavailable("The language model returned an unexpected response.") from exc
        reply: dict[str, Any] = {"content": message.get("content"),
                                 "tool_calls": [_tool_call(c) for c in message.get("tool_calls") or []]}
        usage = _usage(data)
        return {**reply, "usage": usage} if usage else reply


class GroqClient(OpenAICompatClient):
    """Groq with a chosen model; kept so existing imports and scripts keep working."""

    def __init__(self, api_key: str, model: str, *, transport: httpx.BaseTransport | None = None):
        super().__init__(replace(PROVIDERS["groq"], model=model), api_key, transport=transport)


# Provider name -> (monotonic time it may be tried again, why it is cooling down). Module-level so
# the next chat request (which builds new clients) skips a provider that just ran out of quota.
_cooldowns: dict[str, tuple[float, ErrorKind]] = {}
_cooldown_lock = threading.Lock()


def clear_cooldowns() -> None:
    with _cooldown_lock:
        _cooldowns.clear()


class FallbackClient:
    """Tries each client in order; a rate-limited or failing provider cools down and the next answers.

    A rejected tool call (the model's own mistake) is raised straight away. A too-large request
    only moves on to a provider that accepts longer requests than the one that refused it.
    """

    def __init__(self, clients: Sequence[OpenAICompatClient], clock: Callable[[], float] = time.monotonic):
        if not clients:
            raise ValueError("FallbackClient needs at least one client")
        self._clients = tuple(clients)
        self._clock = clock
        self._answered: OpenAICompatClient | None = None

    @property
    def profile(self) -> ProviderProfile:
        """The provider the next request goes to: the first one not cooling down, else the one ready soonest.

        The agent sizes each request with it, so after a rate-limit wait the request fits whichever
        provider answers then (not a bigger one that answered before and is still cooling down).
        """
        return min(self._clients, key=lambda c: (self._ready_in(c), self._clients.index(c))).profile

    @property
    def last_provider(self) -> str | None:
        return self._answered.profile.name if self._answered else None

    def close(self) -> None:
        for client in self._clients:
            close = getattr(client, "close", None)
            if close:
                close()

    def __enter__(self) -> "FallbackClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _cooldown(self, client: OpenAICompatClient) -> tuple[float, ErrorKind]:
        with _cooldown_lock:
            until, kind = _cooldowns.get(client.profile.name, (0.0, "provider"))
        return max(0.0, until - self._clock()), kind

    def _ready_in(self, client: OpenAICompatClient) -> float:
        return self._cooldown(client)[0]

    def _cool_down(self, client: OpenAICompatClient, seconds: float, kind: ErrorKind) -> None:
        with _cooldown_lock:
            _cooldowns[client.profile.name] = (self._clock() + seconds, kind)

    def complete(self, messages: list[dict], tools: list[dict] | None) -> dict:
        estimate = estimate_tokens(messages, tools)
        last_error: AgentUnavailable | None = None
        too_large: AgentUnavailable | None = None
        too_small: set[str] = set()          # providers that can't take this request
        refused_at = 0                       # largest max_input_tokens that answered "too large"
        for client in self._clients:
            limit = client.profile.max_input_tokens
            if limit <= refused_at or limit * SKIP_OVER_BUDGET < estimate:
                too_small.add(client.profile.name)
                continue
            if self._ready_in(client) > 0:
                continue
            try:
                response = client.complete(messages, tools)
            except AgentUnavailable as exc:
                if exc.kind == "too_large":
                    too_small.add(client.profile.name)
                    refused_at, too_large = max(refused_at, limit), exc
                else:
                    self._cool_down(client, exc.retry_after or PROVIDER_DOWN_COOLDOWN, exc.kind)
                    last_error = exc
                continue
            self._answered = client
            return response
        return self._exhausted(last_error, too_large, too_small)

    def _exhausted(self, last_error: AgentUnavailable | None, too_large: AgentUnavailable | None,
                   too_small: set[str]) -> dict:
        cooling = [self._cooldown(c) for c in self._clients if c.profile.name not in too_small]
        cooling = [entry for entry in cooling if entry[0] > 0]
        # Groq at its per-minute limit and Gemini down: the limit surely passes, so it's a rate limit
        # (the agent waits it out), retried as soon as either provider may answer.
        limited = any(kind == "rate_limit" for _, kind in cooling)
        if last_error and not limited:
            raise last_error
        if not cooling:                      # only providers too small for this request were left
            raise too_large or AgentUnavailable(TOO_LARGE_MESSAGE, kind="too_large")
        wait = min(seconds for seconds, _ in cooling)
        seconds = max(1, round(wait))
        if not limited:
            raise AgentUnavailable(f"The AI providers are unreachable right now. Try again in about {seconds} "
                                   "seconds.", kind="provider", retry_after=seconds)
        raise AgentUnavailable(f"The AI providers' free-tier limits were reached. Try again in about {seconds} "
                               "seconds.", kind="rate_limit", retry_after=seconds)


def estimate_tokens(messages: Sequence[dict], tools: Sequence[dict] | None) -> int:
    """Rough request size (chars / 4), enough to skip providers that would refuse it."""
    return len(json.dumps([list(messages), list(tools or [])], default=str)) // CHARS_PER_TOKEN


LLMClientLike = OpenAICompatClient | FallbackClient


def has_llm_provider(settings: Any) -> bool:
    """Whether `settings.llm_chain` names at least one usable provider (no client is built)."""
    for name in (part.strip().lower() for part in str(settings.llm_chain).split(",")):
        profile = PROVIDERS.get(name)
        if profile and (not profile.key_env or getattr(settings, f"{name}_api_key", None)):
            return True
    return False


def build_llm_client(settings: Any, *, transport: httpx.BaseTransport | None = None) -> LLMClientLike | None:
    """Clients for `settings.llm_chain` in order, skipping providers without a key. None if none usable."""
    clients = []
    for name in (part.strip().lower() for part in str(settings.llm_chain).split(",")):
        profile = PROVIDERS.get(name)
        if not profile:
            if name:
                log.warning("Unknown provider %r in LLM_CHAIN; skipped", name)
            continue
        key = getattr(settings, f"{name}_api_key", None) if profile.key_env else None
        if profile.key_env and not key:
            continue
        overrides = {"model": getattr(settings, f"{name}_model", None) or profile.model}
        if base_url := getattr(settings, f"{name}_base_url", None):
            overrides["base_url"] = base_url
        clients.append(OpenAICompatClient(replace(profile, **overrides), key, transport=transport))
    if not clients:
        return None
    return clients[0] if len(clients) == 1 else FallbackClient(clients)

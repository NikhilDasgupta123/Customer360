"""Small, dependency-free Ollama client for CustomerGraph AI agents.

The client deliberately uses Ollama's local REST API rather than an SDK so the
existing backend does not need a new Python package. Agent responses use
Ollama structured output (a JSON schema) and ``stream: false`` so a background
agent can validate one complete JSON object before saving any insight.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from customergraph.core.config import get_settings
from customergraph.core.logging import get_logger

logger = get_logger("customergraph.llm")


class OllamaError(RuntimeError):
    """Base class for safe, user-facing Ollama integration failures."""


class OllamaDisabledError(OllamaError):
    """Raised when an operator has disabled local LLM calls."""


class OllamaUnavailableError(OllamaError):
    """Raised when Ollama cannot be contacted at the configured URL."""


class OllamaModelUnavailableError(OllamaError):
    """Raised when the configured model has not been pulled into Ollama."""


class OllamaResponseError(OllamaError):
    """Raised when Ollama returns an invalid or non-JSON structured response."""


@dataclass(frozen=True)
class OllamaStructuredResponse:
    """Validated transport metadata returned with one structured LLM result."""

    content: dict[str, Any]
    model: str
    total_duration_ms: int | None = None
    prompt_eval_count: int | None = None
    eval_count: int | None = None


def _api_base_url() -> str:
    """Return an Ollama API base URL, accepting values with or without /api."""
    configured = get_settings().ollama_base_url.strip().rstrip("/")
    if not configured:
        configured = "http://127.0.0.1:11434/api"
    return configured if configured.endswith("/api") else f"{configured}/api"


def _safe_error_body(raw_body: bytes) -> str:
    """Extract a short useful error without logging huge/unsafe server bodies."""
    if not raw_body:
        return "No response body."
    try:
        parsed = json.loads(raw_body.decode("utf-8", errors="replace"))
        if isinstance(parsed, dict):
            return str(parsed.get("error") or parsed.get("message") or "Unexpected Ollama error.")[:300]
    except json.JSONDecodeError:
        pass
    return raw_body.decode("utf-8", errors="replace").strip()[:300] or "Unexpected Ollama error."


def _request_json(path: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call the local Ollama API and return a JSON object with safe errors."""
    settings = get_settings()
    url = f"{_api_base_url()}{path}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        url,
        data=body,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )

    try:
        with urlopen(request, timeout=settings.ollama_request_timeout_seconds) as response:  # nosec B310 - operator-configured local endpoint
            raw_response = response.read()
    except HTTPError as exc:
        error_body = _safe_error_body(exc.read())
        if exc.code == 404 and path in {"/chat", "/generate"}:
            raise OllamaModelUnavailableError(
                f"Ollama model '{settings.ollama_model}' is not available. Run: ollama pull {settings.ollama_model}"
            ) from exc
        raise OllamaUnavailableError(f"Ollama returned HTTP {exc.code}: {error_body}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise OllamaUnavailableError(
            f"Ollama is unavailable at {_api_base_url()}. Start Ollama and verify OLLAMA_BASE_URL."
        ) from exc

    try:
        parsed = json.loads(raw_response.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise OllamaResponseError("Ollama returned a non-JSON response.") from exc

    if not isinstance(parsed, dict):
        raise OllamaResponseError("Ollama returned an invalid JSON response shape.")
    if parsed.get("error"):
        raise OllamaResponseError(str(parsed["error"])[:300])
    return parsed


def _model_names(tags_response: dict[str, Any]) -> set[str]:
    """Read model names from Ollama's /api/tags response across versions."""
    models = tags_response.get("models", [])
    if not isinstance(models, list):
        return set()

    names: set[str] = set()
    for model in models:
        if isinstance(model, dict):
            for key in ("name", "model"):
                value = model.get(key)
                if isinstance(value, str) and value.strip():
                    names.add(value.strip())
    return names


def get_ollama_health() -> dict[str, Any]:
    """Return a safe health payload for the Admin agent-settings screen."""
    settings = get_settings()
    if not settings.ollama_enabled:
        return {
            "ok": False,
            "enabled": False,
            "base_url": _api_base_url(),
            "model": settings.ollama_model,
            "model_available": False,
            "message": "Ollama is disabled by OLLAMA_ENABLED=false.",
        }

    try:
        models = _model_names(_request_json("/tags"))
        model_available = settings.ollama_model in models
        return {
            "ok": model_available,
            "enabled": True,
            "base_url": _api_base_url(),
            "model": settings.ollama_model,
            "model_available": model_available,
            "available_models": sorted(models),
            "message": (
                "Ollama is ready."
                if model_available
                else f"Configured model '{settings.ollama_model}' is not pulled yet."
            ),
        }
    except OllamaError as exc:
        return {
            "ok": False,
            "enabled": True,
            "base_url": _api_base_url(),
            "model": settings.ollama_model,
            "model_available": False,
            "message": str(exc),
        }


def _strip_json_fence(text: str) -> str:
    """Tolerate an occasional Markdown fence while still requiring valid JSON."""
    cleaned = text.strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1]).strip()
    return cleaned


def generate_structured_response(
    *,
    system_prompt: str,
    user_prompt: str,
    json_schema: dict[str, Any],
) -> OllamaStructuredResponse:
    """Ask Ollama for one schema-constrained JSON result.

    This never sends database credentials or raw graph queries. Callers pass a
    minimal, already-authorized customer context and validate the parsed output
    again with Pydantic before it is saved.
    """
    settings = get_settings()
    if not settings.ollama_enabled:
        raise OllamaDisabledError("Ollama is disabled. Set OLLAMA_ENABLED=true to run AI agents.")

    # Readiness is checked once before a run is queued. Avoid a separate
    # /api/tags round trip for every customer in a large portfolio run; a model
    # removed between queueing and generation is still converted into a safe
    # OllamaModelUnavailableError by the /api/chat HTTP error handler.
    response = _request_json(
        "/chat",
        method="POST",
        payload={
            "model": settings.ollama_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "format": json_schema,
            "stream": False,
            # We need concise, reviewable business outputs, not a reasoning trace.
            "think": False,
            "keep_alive": settings.ollama_keep_alive,
            "options": {"temperature": settings.ollama_temperature},
        },
    )

    message = response.get("message")
    content_text = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content_text, str) or not content_text.strip():
        raise OllamaResponseError("Ollama returned no structured message content.")

    try:
        content = json.loads(_strip_json_fence(content_text))
    except json.JSONDecodeError as exc:
        raise OllamaResponseError("Ollama returned invalid JSON for the requested agent schema.") from exc

    if not isinstance(content, dict):
        raise OllamaResponseError("Ollama structured response must be a JSON object.")

    total_duration = response.get("total_duration")
    return OllamaStructuredResponse(
        content=content,
        model=str(response.get("model") or settings.ollama_model),
        total_duration_ms=(int(total_duration / 1_000_000) if isinstance(total_duration, int) else None),
        prompt_eval_count=(int(response["prompt_eval_count"]) if isinstance(response.get("prompt_eval_count"), int) else None),
        eval_count=(int(response["eval_count"]) if isinstance(response.get("eval_count"), int) else None),
    )

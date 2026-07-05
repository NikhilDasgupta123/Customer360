"""Small, UI-focused CustomerGraph AI analysis API.

Only two public endpoints are exposed:
* One Dashboard endpoint; a ``section`` query value identifies the clicked card
  or panel. ``stream=true`` keeps the same endpoint but returns SSE lifecycle
  and validated-result events for the Dashboard drawer.
* One Customers endpoint for an individual customer brief.

There are no public queue, run-status, health, or "latest insight" endpoints.
"""

from __future__ import annotations

import json
import queue
import re
import threading
import time
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from neo4j.exceptions import Neo4jError

from customergraph.agents.data_service import CustomerAnalysisAccessError
from customergraph.agents.schemas import (
    DashboardAnalysisSection,
    DashboardWidgetAnalysisResponse,
    SimpleCustomerAnalysisResponse,
)
from customergraph.agents.service import (
    AgentActor,
    AgentRunConflictError,
    AgentUnavailableError,
    analyse_customer_now,
    analyse_dashboard_widget_now,
)
from customergraph.auth.dependencies import CurrentUser, require_module, require_roles
from customergraph.core.logging import get_logger
from customergraph.models.user import UserRole

router = APIRouter(prefix="/ai", tags=["AI Analysis"])
logger = get_logger("customergraph.ai.api")
admin_only = [Depends(require_roles(UserRole.ADMIN))]
customer_ai_access = [Depends(require_module("customers"))]


def _service_unavailable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)


def _sse(event: str, payload: dict[str, Any]) -> str:
    """Create one standards-compatible Server-Sent Event frame."""
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    return f"event: {event}\ndata: {encoded}\n\n"


def _brief_chunks(text: str) -> list[str]:
    """Split validated text into short, readable UI chunks.

    This is presentation pacing only: the browser still receives only
    backend-validated business text and never raw model tokens.
    """
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return []

    # Keep the same sentence order, but reveal no more than four words per SSE
    # event. The browser also applies an independent display queue, so this
    # stays readable even when a local proxy buffers several SSE frames.
    words = cleaned.split(" ")
    chunks: list[str] = []
    current: list[str] = []
    for word in words:
        current.append(word)
        sentence_end = word.endswith((".", "!", "?"))
        if len(current) >= 4 or (sentence_end and len(current) >= 3):
            chunks.append(" ".join(current) + " ")
            current = []
    if current:
        chunks.append(" ".join(current))
    return chunks



# Deliberate presentation pacing keeps each UI stage visible on fast local
# models. These delays are not model thinking time and never reveal internals.
# Server pacing keeps direct Swagger/SSE consumers readable. The frontend has
# its own queue as an additional guarantee, so buffered chunks cannot race.
_MIN_PROGRESS_STAGE_SECONDS = 1.35
_BRIEF_CHUNK_SECONDS = 0.46
_SIGNAL_REVEAL_SECONDS = 0.90
_ACTION_REVEAL_SECONDS = 0.85


def _stream_dashboard_widget_analysis(
    section: DashboardAnalysisSection,
    actor: AgentActor,
) -> Iterator[str]:
    """Stream lifecycle events and then the validated widget response.

    The agent still performs one controlled structured LLM call. The public
    API remains the same URL; ``stream=true`` only changes the transport to
    SSE so the UI can show an honest live analysis state and progressively
    reveal the final validated content.
    """
    events: queue.Queue[tuple[str, dict[str, Any] | None]] = queue.Queue()

    def publish(stage: str, message: str, progress: int) -> None:
        events.put(("progress", {"stage": stage, "message": message, "progress": progress}))

    def run_analysis() -> None:
        try:
            result = analyse_dashboard_widget_now(section, actor, progress_callback=publish)
            events.put(("result", result.model_dump(mode="json")))
        except AgentRunConflictError:
            events.put(("error", {"message": "This dashboard analysis is already running. Please wait for it to finish.", "status_code": 409}))
        except AgentUnavailableError as exc:
            events.put(("error", {"message": str(exc), "status_code": 503}))
        except (Neo4jError, OSError):
            logger.exception("dashboard_widget_ai_stream_graph_failed section=%s", section.value)
            events.put(("error", {"message": "AI analysis is unavailable. Check Neo4j and Ollama.", "status_code": 503}))
        except Exception:
            logger.exception("dashboard_widget_ai_stream_failed section=%s", section.value)
            events.put(("error", {"message": "Dashboard AI analysis could not be completed. Check the server logs.", "status_code": 500}))
        finally:
            events.put(("done", None))

    worker = threading.Thread(target=run_analysis, name=f"dashboard-ai-{section.value}", daemon=True)
    worker.start()

    # Helpful for proxies and browsers: establish the SSE channel immediately.
    yield ": customergraph-ai-stream-connected\n\n"
    last_progress_emitted_at = 0.0

    while True:
        event, payload = events.get()
        if event == "progress" and payload is not None:
            # On a local model all stages can complete almost instantly. Keep
            # every stage visible long enough for a person to read it.
            now = time.monotonic()
            if last_progress_emitted_at:
                remaining = _MIN_PROGRESS_STAGE_SECONDS - (now - last_progress_emitted_at)
                if remaining > 0:
                    time.sleep(remaining)
            yield _sse("progress", payload)
            last_progress_emitted_at = time.monotonic()
            continue

        if event == "result" and payload is not None:
            # Metadata first, then the brief/signal/action blocks. The UI can
            # render these progressively without ever seeing malformed JSON.
            yield _sse("status", {
                "section": payload.get("section"),
                "title": payload.get("title"),
                "status": payload.get("status"),
                "generated_at": payload.get("generated_at"),
            })
            # Let the final validation stage remain visible before the brief starts.
            time.sleep(_ACTION_REVEAL_SECONDS)
            for chunk in _brief_chunks(str(payload.get("summary") or "")):
                yield _sse("brief_chunk", {"text": chunk})
                time.sleep(_BRIEF_CHUNK_SECONDS)
            for index, signal in enumerate(payload.get("evidence") or [], start=1):
                yield _sse("signal", {"index": index, "text": signal})
                time.sleep(_SIGNAL_REVEAL_SECONDS)
            yield _sse("recommended_action", {"text": str(payload.get("recommended_action") or "")})
            time.sleep(_ACTION_REVEAL_SECONDS)
            yield _sse("complete", payload)
            continue

        if event == "error" and payload is not None:
            yield _sse("error", payload)
            continue

        if event == "done":
            break


@router.post(
    "/analyse-dashboard",
    response_model=DashboardWidgetAnalysisResponse,
    summary="Analyse one Dashboard widget with AI",
    dependencies=admin_only,
)
def analyse_dashboard(
    current_user: CurrentUser,
    section: DashboardAnalysisSection = Query(
        ...,
        description="The Dashboard card or panel selected by the user.",
    ),
    stream: bool = Query(
        False,
        description="When true, return Server-Sent Events for live UI progress and validated result blocks.",
    ),
):
    """One API for all Dashboard sparkle buttons; request body is empty.

    ``stream=false`` is the existing Swagger-friendly JSON response. The UI
    sends ``stream=true`` and consumes the same response as Server-Sent Events.
    """
    actor = AgentActor.from_current_user(current_user)
    if stream:
        return StreamingResponse(
            _stream_dashboard_widget_analysis(section, actor),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        return analyse_dashboard_widget_now(section, actor)
    except AgentRunConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This dashboard analysis is already running. Please wait for it to finish.",
        ) from exc
    except AgentUnavailableError as exc:
        raise _service_unavailable(str(exc)) from exc
    except (Neo4jError, OSError) as exc:
        logger.exception("dashboard_widget_ai_graph_failed user_id=%s section=%s", current_user.id, section.value)
        raise _service_unavailable("AI analysis is unavailable. Check Neo4j and Ollama.") from exc
    except Exception as exc:
        logger.exception("dashboard_widget_ai_failed user_id=%s section=%s", current_user.id, section.value)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Dashboard AI analysis could not be completed. Check the server logs.",
        ) from exc


@router.post(
    "/customers/{customer_id}/analyse",
    response_model=SimpleCustomerAnalysisResponse,
    summary="Analyse one customer with AI",
    dependencies=customer_ai_access,
)
def analyse_customer(customer_id: str, current_user: CurrentUser) -> SimpleCustomerAnalysisResponse:
    """One Customers button: actual customer data -> LLM summary and next action."""
    try:
        return analyse_customer_now(customer_id, AgentActor.from_current_user(current_user))
    except CustomerAnalysisAccessError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer was not found.") from exc
    except AgentRunConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="AI analysis is already running for this customer. Please wait for it to finish.",
        ) from exc
    except AgentUnavailableError as exc:
        raise _service_unavailable(str(exc)) from exc
    except (Neo4jError, OSError) as exc:
        logger.exception("customer_ai_analysis_graph_failed user_id=%s customer_id=%s", current_user.id, customer_id)
        raise _service_unavailable("AI analysis is unavailable. Check Neo4j and Ollama.") from exc
    except Exception as exc:
        logger.exception("customer_ai_analysis_failed user_id=%s customer_id=%s", current_user.id, customer_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Customer AI analysis could not be completed. Check the server logs.",
        ) from exc

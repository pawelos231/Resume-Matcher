"""Search scraping endpoints."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.schemas import (
    SearchGenerateJobDescriptionRequest,
    SearchGenerateJobDescriptionResponse,
    SearchScrapeResponse,
    SearchStopRequest,
    SearchStopResponse,
)
from app.services.search.offer_generation import (
    OfferJobDescriptionInput,
    generate_job_description_from_offer,
)
from app.services.search.pipeline import parse_stream_mode, run_scrape

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/search", tags=["Search"])
_ACTIVE_SCRAPE_STOPS: dict[str, asyncio.Event] = {}
_ACTIVE_SCRAPE_STOPS_LOCK = asyncio.Lock()


def _format_sse(event: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


def _get_request_id(params: Mapping[str, str]) -> str | None:
    raw = params.get("requestId")
    if raw is None:
        return None
    request_id = raw.strip()
    return request_id or None


async def _register_scrape_stop(request_id: str | None) -> asyncio.Event | None:
    if request_id is None:
        return None

    stop_event = asyncio.Event()
    async with _ACTIVE_SCRAPE_STOPS_LOCK:
        _ACTIVE_SCRAPE_STOPS[request_id] = stop_event
    return stop_event


async def _unregister_scrape_stop(request_id: str | None) -> None:
    if request_id is None:
        return

    async with _ACTIVE_SCRAPE_STOPS_LOCK:
        _ACTIVE_SCRAPE_STOPS.pop(request_id, None)


async def _request_scrape_stop(request_id: str) -> bool:
    async with _ACTIVE_SCRAPE_STOPS_LOCK:
        stop_event = _ACTIVE_SCRAPE_STOPS.get(request_id)

    if stop_event is None:
        return False

    stop_event.set()
    return True


@router.post("/scrape/stop", response_model=SearchStopResponse)
async def stop_scrape(
    request: SearchStopRequest,
) -> SearchStopResponse:
    """Request an active scrape run to stop and return partial results."""
    stop_requested = await _request_scrape_stop(request.requestId)
    return SearchStopResponse(
        requestId=request.requestId,
        stopRequested=stop_requested,
    )


@router.get("/scrape", response_model=SearchScrapeResponse)
async def scrape_offers(request: Request):
    """Scrape job offers from configured providers."""
    params: Mapping[str, str] = request.query_params
    request_id = _get_request_id(params)
    if not parse_stream_mode(params):
        stop_event = await _register_scrape_stop(request_id)
        try:
            try:
                status, payload = await run_scrape(params, stop_event=stop_event)
                return JSONResponse(status_code=status, content=payload)
            except Exception as exc:
                logger.exception("Search scrape failed")
                return JSONResponse(
                    status_code=500,
                    content={
                        "message": "Scraping failed",
                        "error": str(exc),
                    },
                )
        finally:
            await _unregister_scrape_stop(request_id)

    stop_event = await _register_scrape_stop(request_id)
    queue: asyncio.Queue[tuple[str, dict[str, Any] | None]] = asyncio.Queue()

    def _on_progress(event: dict[str, Any]) -> None:
        queue.put_nowait(("progress", event))

    async def _runner() -> None:
        try:
            status, payload = await run_scrape(
                params,
                _on_progress,
                stop_event=stop_event,
            )
            queue.put_nowait(("done", {"status": status, "payload": payload}))
        except Exception as exc:
            logger.exception("Search scrape stream failed")
            queue.put_nowait(("error", {"message": str(exc)}))
        finally:
            queue.put_nowait(("close", None))

    async def _stream():
        task = asyncio.create_task(_runner())
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event, payload = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield b": keep-alive\n\n"
                    continue

                if event == "close":
                    break
                if payload is None:
                    continue
                yield _format_sse(event, payload)
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await _unregister_scrape_stop(request_id)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/generate-job-description",
    response_model=SearchGenerateJobDescriptionResponse,
)
async def generate_offer_job_description(
    request: SearchGenerateJobDescriptionRequest,
) -> SearchGenerateJobDescriptionResponse:
    """Extract offer content and generate a tailor-ready job description."""
    offer = OfferJobDescriptionInput(
        source=request.source,
        title=request.title,
        company=request.company,
        location=request.location,
        salary=request.salary,
        url=request.url,
        skills=request.skills,
    )

    try:
        payload = await generate_job_description_from_offer(offer)
        return SearchGenerateJobDescriptionResponse(**payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Search offer job-description generation failed")
        raise HTTPException(
            status_code=500,
            detail="Failed to generate job description. Please try again.",
        ) from exc

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
)
from app.services.search.offer_generation import (
    OfferJobDescriptionInput,
    generate_job_description_from_offer,
)
from app.services.search.pipeline import parse_stream_mode, run_scrape

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/search", tags=["Search"])


def _format_sse(event: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


@router.get("/scrape", response_model=SearchScrapeResponse)
async def scrape_offers(request: Request):
    """Scrape job offers from configured providers."""
    params: Mapping[str, str] = request.query_params

    if not parse_stream_mode(params):
        try:
            status, payload = await run_scrape(params)
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

    queue: asyncio.Queue[tuple[str, dict[str, Any] | None]] = asyncio.Queue()

    def _on_progress(event: dict[str, Any]) -> None:
        queue.put_nowait(("progress", event))

    async def _runner() -> None:
        try:
            status, payload = await run_scrape(params, _on_progress)
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

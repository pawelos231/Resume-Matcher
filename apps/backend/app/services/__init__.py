"""Business logic services."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = [
    "parse_document",
    "parse_resume_to_json",
    "improve_resume",
    "generate_improvements",
    "refine_resume",
]

if TYPE_CHECKING:
    from app.services.improver import generate_improvements, improve_resume
    from app.services.parser import parse_document, parse_resume_to_json
    from app.services.refiner import refine_resume


def __getattr__(name: str) -> Any:
    if name in {"parse_document", "parse_resume_to_json"}:
        from app.services.parser import parse_document, parse_resume_to_json

        exports = {
            "parse_document": parse_document,
            "parse_resume_to_json": parse_resume_to_json,
        }
        return exports[name]

    if name in {"improve_resume", "generate_improvements"}:
        from app.services.improver import generate_improvements, improve_resume

        exports = {
            "improve_resume": improve_resume,
            "generate_improvements": generate_improvements,
        }
        return exports[name]

    if name == "refine_resume":
        from app.services.refiner import refine_resume

        return refine_resume

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

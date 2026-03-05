"""Document parsing service using markitdown and LLM."""

import tempfile
from pathlib import Path
from typing import Any

from markitdown import MarkItDown

from app.llm import complete_json
from app.prompts import PARSE_RESUME_PROMPT
from app.prompts.templates import RESUME_EXTRACTION_SCHEMA_TEMPLATE
from app.schemas import ResumeData


async def parse_document(content: bytes, filename: str) -> str:
    """Convert PDF/DOCX to Markdown using markitdown.

    Args:
        content: Raw file bytes
        filename: Original filename for extension detection

    Returns:
        Markdown text content
    """
    suffix = Path(filename).suffix.lower()

    # Write to temp file for markitdown
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        md = MarkItDown()
        result = md.convert(str(tmp_path))
        return result.text_content
    finally:
        tmp_path.unlink(missing_ok=True)


async def parse_resume_to_json(markdown_text: str) -> dict[str, Any]:
    """Parse resume markdown to structured JSON using LLM.

    Args:
        markdown_text: Resume content in markdown format

    Returns:
        Structured resume data matching ResumeData schema
    """
    prompt = PARSE_RESUME_PROMPT.format(
        schema=RESUME_EXTRACTION_SCHEMA_TEMPLATE,
        resume_text=markdown_text,
    )

    result = await complete_json(
        prompt=prompt,
        system_prompt=(
            "You are a strict resume-to-JSON mapper. "
            "Map only what is explicitly visible in input text. "
            "Do not infer, embellish, normalize, or rewrite content. "
            "If something is missing, keep it empty. "
            "Return only one valid JSON object with schema-compatible keys."
        ),
        max_tokens=8192,
        retries=1,
        deterministic=True,
    )

    # Validate against schema
    validated = ResumeData.model_validate(result)
    return validated.model_dump()

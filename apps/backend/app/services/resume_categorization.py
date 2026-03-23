"""Heuristic resume categorization based on keyword matches."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

_CATEGORY_ORDER = (
    "fullstack",
    "frontend",
    "backend",
    "mobile",
    "data",
    "devops",
    "qa",
    "uiux",
    "uncategorized",
)
_CATEGORY_INDEX = {category: index for index, category in enumerate(_CATEGORY_ORDER)}
_WHITESPACE_RE = re.compile(r"\s+")
_NORMALIZE_RE = re.compile(r"[^a-z0-9+#.]+")
_BOUNDARY_TEMPLATE = r"(?<![a-z0-9]){keyword}(?![a-z0-9])"
_TEXT_KEYS = (
    "title",
    "summary",
    "description",
    "name",
    "role",
    "company",
    "institution",
    "degree",
    "text",
    "subtitle",
    "label",
    "content",
    "technicalSkills",
    "languages",
    "certificationsTraining",
    "awards",
)

_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "frontend": (
        "frontend",
        "front end",
        "react",
        "next.js",
        "nextjs",
        "vue",
        "angular",
        "html",
        "css",
        "tailwind",
        "sass",
        "scss",
        "redux",
        "storybook",
        "webpack",
        "vite",
        "responsive design",
        "web accessibility",
    ),
    "backend": (
        "backend",
        "back end",
        "fastapi",
        "django",
        "flask",
        "spring",
        "spring boot",
        "laravel",
        "nestjs",
        "nest.js",
        "express",
        "node.js",
        "nodejs",
        "rest api",
        "graphql",
        "microservices",
        "postgresql",
        "mysql",
        "redis",
        "kafka",
        "rabbitmq",
        "sqlalchemy",
    ),
    "fullstack": (
        "full stack",
        "full-stack",
        "fullstack",
        "mern",
        "mean",
        "jamstack",
    ),
    "mobile": (
        "mobile",
        "android",
        "ios",
        "react native",
        "flutter",
        "swift",
        "kotlin",
        "xcode",
        "app store",
        "play store",
    ),
    "data": (
        "data engineer",
        "data science",
        "data scientist",
        "data analyst",
        "machine learning",
        "deep learning",
        "artificial intelligence",
        "ml",
        "ai",
        "pandas",
        "numpy",
        "scikit",
        "pytorch",
        "tensorflow",
        "spark",
        "airflow",
        "dbt",
        "tableau",
        "power bi",
        "bigquery",
    ),
    "devops": (
        "devops",
        "site reliability",
        "sre",
        "docker",
        "kubernetes",
        "terraform",
        "ansible",
        "helm",
        "prometheus",
        "grafana",
        "aws",
        "azure",
        "gcp",
        "github actions",
        "gitlab ci",
        "ci cd",
        "continuous integration",
        "continuous delivery",
    ),
    "qa": (
        "qa",
        "quality assurance",
        "test automation",
        "automation tester",
        "manual testing",
        "selenium",
        "cypress",
        "playwright",
        "regression testing",
        "e2e testing",
        "end to end testing",
    ),
    "uiux": (
        "ui ux",
        "ux",
        "ui",
        "product design",
        "user experience",
        "user interface",
        "interaction design",
        "design system",
        "wireframing",
        "prototype",
        "figma",
        "adobe xd",
    ),
}


def categorize_resume_record(resume: dict[str, Any]) -> tuple[list[str], str]:
    """Categorize a resume record using structured data and raw content."""
    searchable_text = _build_resume_searchable_text(resume)
    return categorize_resume_text(searchable_text)


def categorize_resume_text(text: str) -> tuple[list[str], str]:
    """Categorize resume text and return ordered category ids and the primary one."""
    normalized_text = _normalize_text(text)
    if not normalized_text:
        return ["uncategorized"], "uncategorized"

    scores: dict[str, int] = {}
    for category, keywords in _CATEGORY_KEYWORDS.items():
        scores[category] = _count_keyword_matches(normalized_text, keywords)

    if scores["frontend"] > 0 and scores["backend"] > 0:
        scores["fullstack"] = max(
            scores["fullstack"],
            min(scores["frontend"], scores["backend"]) + 1,
        )

    ranked_categories = [
        category
        for category, score in sorted(
            scores.items(),
            key=lambda item: (-item[1], _CATEGORY_INDEX.get(item[0], len(_CATEGORY_INDEX))),
        )
        if score > 0
    ]

    if not ranked_categories:
        return ["uncategorized"], "uncategorized"

    categories = ranked_categories[:3]
    return categories, categories[0]


def _build_resume_searchable_text(resume: dict[str, Any]) -> str:
    fragments: list[str] = []

    processed_data = resume.get("processed_data")
    if isinstance(processed_data, dict):
        fragments.extend(_extract_text_fragments(processed_data))

    for field_name in ("title", "filename", "content"):
        value = resume.get(field_name)
        if isinstance(value, str) and value.strip():
            fragments.append(value.strip())

    return "\n".join(fragments)


def _extract_text_fragments(
    value: Any,
    *,
    depth: int = 0,
    max_depth: int = 10,
) -> list[str]:
    if depth >= max_depth or value is None:
        return []

    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []

    if isinstance(value, (int, float, bool)):
        return [str(value)]

    if isinstance(value, list):
        fragments: list[str] = []
        for item in value:
            fragments.extend(
                _extract_text_fragments(item, depth=depth + 1, max_depth=max_depth)
            )
        return fragments

    if isinstance(value, dict):
        fragments: list[str] = []

        for key in _TEXT_KEYS:
            if key in value:
                fragments.extend(
                    _extract_text_fragments(
                        value.get(key), depth=depth + 1, max_depth=max_depth
                    )
                )

        if fragments:
            return fragments

        for nested_value in value.values():
            fragments.extend(
                _extract_text_fragments(
                    nested_value, depth=depth + 1, max_depth=max_depth
                )
            )
        return fragments

    return []


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower().replace("&", " and ")
    collapsed = _NORMALIZE_RE.sub(" ", lowered)
    return _WHITESPACE_RE.sub(" ", collapsed).strip()


def _count_keyword_matches(text: str, keywords: tuple[str, ...]) -> int:
    matches = {
        normalized_keyword
        for keyword in keywords
        if (normalized_keyword := _normalize_text(keyword))
        and _matches_keyword(text, normalized_keyword)
    }
    return len(matches)


def _matches_keyword(text: str, keyword: str) -> bool:
    pattern = _BOUNDARY_TEMPLATE.format(keyword=re.escape(keyword))
    return re.search(pattern, text) is not None

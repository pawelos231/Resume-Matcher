"""TinyDB database layer for JSON storage."""

import asyncio
import copy
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from tinydb import Query, TinyDB
from tinydb.table import Table

from app.config import settings

logger = logging.getLogger(__name__)


class Database:
    """TinyDB wrapper for resume matcher data."""

    _master_resume_lock = asyncio.Lock()

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or settings.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db: TinyDB | None = None

    @property
    def db(self) -> TinyDB:
        """Lazy initialization of TinyDB instance."""
        if self._db is None:
            self._db = TinyDB(self.db_path)
        return self._db

    @property
    def resumes(self) -> Table:
        """Resumes table."""
        return self.db.table("resumes")

    @property
    def jobs(self) -> Table:
        """Job descriptions table."""
        return self.db.table("jobs")

    @property
    def improvements(self) -> Table:
        """Improvement results table."""
        return self.db.table("improvements")

    @property
    def search_cache(self) -> Table:
        """Persistent search-result cache table."""
        return self.db.table("search_cache")

    @property
    def company_info_cache(self) -> Table:
        """Persistent company-info cache table."""
        return self.db.table("company_info_cache")

    @property
    def offer_resume_cache(self) -> Table:
        """Persistent offer-to-resume cache table."""
        return self.db.table("offer_resume_cache")

    def close(self) -> None:
        """Close database connection."""
        if self._db is not None:
            self._db.close()
            self._db = None

    # Resume operations
    def create_resume(
        self,
        content: str,
        content_type: str = "md",
        filename: str | None = None,
        is_master: bool = False,
        parent_id: str | None = None,
        processed_data: dict[str, Any] | None = None,
        processing_status: str = "pending",
        cover_letter: str | None = None,
        outreach_message: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Create a new resume entry.

        processing_status: "pending", "processing", "ready", "failed"
        """
        resume_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()

        doc = {
            "resume_id": resume_id,
            "content": content,
            "content_type": content_type,
            "filename": filename,
            "is_master": is_master,
            "parent_id": parent_id,
            "processed_data": processed_data,
            "processing_status": processing_status,
            "cover_letter": cover_letter,
            "outreach_message": outreach_message,
            "title": title,
            "created_at": now,
            "updated_at": now,
        }
        self.resumes.insert(doc)
        return doc

    async def create_resume_atomic_master(
        self,
        content: str,
        content_type: str = "md",
        filename: str | None = None,
        processed_data: dict[str, Any] | None = None,
        processing_status: str = "pending",
        cover_letter: str | None = None,
        outreach_message: str | None = None,
    ) -> dict[str, Any]:
        """Create a new resume with atomic master assignment.

        Uses an asyncio.Lock to prevent race conditions when multiple uploads
        happen concurrently and both try to become master. This avoids blocking
        the FastAPI event loop unlike threading.Lock.
        """
        async with self._master_resume_lock:
            current_master = self.get_master_resume()
            is_master = current_master is None

            # Recovery behavior: if the current master is stuck in failed parsing
            # state, promote the next upload to become the new master resume.
            if current_master and current_master.get("processing_status") == "failed":
                Resume = Query()
                self.resumes.update(
                    {"is_master": False},
                    Resume.resume_id == current_master["resume_id"],
                )
                is_master = True

            return self.create_resume(
                content=content,
                content_type=content_type,
                filename=filename,
                is_master=is_master,
                processed_data=processed_data,
                processing_status=processing_status,
                cover_letter=cover_letter,
                outreach_message=outreach_message,
            )

    def get_resume(self, resume_id: str) -> dict[str, Any] | None:
        """Get resume by ID."""
        Resume = Query()
        result = self.resumes.search(Resume.resume_id == resume_id)
        return result[0] if result else None

    def get_master_resume(self) -> dict[str, Any] | None:
        """Get the master resume if exists."""
        Resume = Query()
        result = self.resumes.search(Resume.is_master == True)
        return result[0] if result else None

    def update_resume(self, resume_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Update resume by ID.

        Raises:
            ValueError: If resume not found.
        """
        Resume = Query()
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        updated_count = self.resumes.update(updates, Resume.resume_id == resume_id)

        if not updated_count:
            raise ValueError(f"Resume not found: {resume_id}")

        result = self.get_resume(resume_id)
        if not result:
            raise ValueError(f"Resume disappeared after update: {resume_id}")

        return result

    def delete_resume(self, resume_id: str) -> bool:
        """Delete resume by ID."""
        Resume = Query()
        removed = self.resumes.remove(Resume.resume_id == resume_id)
        return len(removed) > 0

    def list_resumes(self) -> list[dict[str, Any]]:
        """List all resumes."""
        return list(self.resumes.all())

    def set_master_resume(self, resume_id: str) -> bool:
        """Set a resume as the master, unsetting any existing master.

        Returns False if the resume doesn't exist.
        """
        Resume = Query()

        # First verify the target resume exists
        target = self.resumes.search(Resume.resume_id == resume_id)
        if not target:
            logger.warning("Cannot set master: resume %s not found", resume_id)
            return False

        # Unset current master
        self.resumes.update({"is_master": False}, Resume.is_master == True)
        # Set new master
        updated = self.resumes.update(
            {"is_master": True}, Resume.resume_id == resume_id
        )
        return len(updated) > 0

    # Job operations
    def create_job(self, content: str, resume_id: str | None = None) -> dict[str, Any]:
        """Create a new job description entry."""
        job_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()

        doc = {
            "job_id": job_id,
            "content": content,
            "resume_id": resume_id,
            "created_at": now,
        }
        self.jobs.insert(doc)
        return doc

    def create_job_with_offer_marker(
        self,
        content: str,
        resume_id: str | None = None,
        offer_marker: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new job description entry with optional search-offer metadata."""
        job = self.create_job(content=content, resume_id=resume_id)
        if offer_marker is None:
            return job

        return self.update_job(
            job["job_id"],
            {
                "offer_marker": copy.deepcopy(offer_marker),
            },
        ) or job

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        """Get job by ID."""
        Job = Query()
        result = self.jobs.search(Job.job_id == job_id)
        return result[0] if result else None

    def update_job(self, job_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        """Update a job by ID."""
        Job = Query()
        updated = self.jobs.update(updates, Job.job_id == job_id)
        if not updated:
            return None
        return self.get_job(job_id)

    # Persistent cache operations
    def get_cached_search_result(self, cache_key: str) -> dict[str, Any] | None:
        """Return a persistent search cache entry if it exists and is not expired."""
        SearchCache = Query()
        result = self.search_cache.search(SearchCache.cache_key == cache_key)
        if not result:
            return None

        entry = result[0]
        expires_at = entry.get("expires_at")
        if not isinstance(expires_at, (int, float)):
            self.search_cache.remove(SearchCache.cache_key == cache_key)
            return None

        if expires_at <= datetime.now(timezone.utc).timestamp():
            self.search_cache.remove(SearchCache.cache_key == cache_key)
            return None

        return copy.deepcopy(entry)

    def upsert_cached_search_result(
        self,
        cache_key: str,
        status: int,
        payload: dict[str, Any],
        ttl_seconds: int,
    ) -> dict[str, Any]:
        """Store a persistent search cache entry."""
        SearchCache = Query()
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        existing = self.search_cache.search(SearchCache.cache_key == cache_key)
        created_at = existing[0].get("created_at", now_iso) if existing else now_iso
        entry = {
            "cache_key": cache_key,
            "status": status,
            "payload": copy.deepcopy(payload),
            "created_at": created_at,
            "updated_at": now_iso,
            "expires_at": now.timestamp() + max(ttl_seconds, 0),
        }

        if existing:
            self.search_cache.update(entry, SearchCache.cache_key == cache_key)
        else:
            self.search_cache.insert(entry)

        return copy.deepcopy(entry)

    def get_company_info_cache(self, offer_key: str) -> dict[str, Any] | None:
        """Return cached company info for an offer, if present."""
        CompanyInfoCache = Query()
        result = self.company_info_cache.search(CompanyInfoCache.offer_key == offer_key)
        if not result:
            return None
        return copy.deepcopy(result[0])

    def upsert_company_info_cache(
        self,
        offer_key: str,
        offer_marker: dict[str, Any],
        response: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist generated company info for an offer."""
        CompanyInfoCache = Query()
        now_iso = datetime.now(timezone.utc).isoformat()
        existing = self.company_info_cache.search(CompanyInfoCache.offer_key == offer_key)
        created_at = existing[0].get("created_at", now_iso) if existing else now_iso
        entry = {
            "offer_key": offer_key,
            "offer_marker": copy.deepcopy(offer_marker),
            "response": copy.deepcopy(response),
            "created_at": created_at,
            "updated_at": now_iso,
        }

        if existing:
            self.company_info_cache.update(entry, CompanyInfoCache.offer_key == offer_key)
        else:
            self.company_info_cache.insert(entry)

        return copy.deepcopy(entry)

    def get_offer_resume_cache(self, offer_key: str) -> dict[str, Any] | None:
        """Return cached resume mapping for an offer, cleaning up stale entries."""
        OfferResumeCache = Query()
        result = self.offer_resume_cache.search(OfferResumeCache.offer_key == offer_key)
        if not result:
            return None

        entry = result[0]
        resume_id = entry.get("resume_id")
        if not isinstance(resume_id, str) or self.get_resume(resume_id) is None:
            self.offer_resume_cache.remove(OfferResumeCache.offer_key == offer_key)
            return None

        return copy.deepcopy(entry)

    def upsert_offer_resume_cache(
        self,
        offer_key: str,
        resume_id: str,
        offer_marker: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist the tailored resume associated with a search offer."""
        if self.get_resume(resume_id) is None:
            raise ValueError(f"Cannot cache missing resume: {resume_id}")

        OfferResumeCache = Query()
        now_iso = datetime.now(timezone.utc).isoformat()
        existing = self.offer_resume_cache.search(OfferResumeCache.offer_key == offer_key)
        created_at = existing[0].get("created_at", now_iso) if existing else now_iso
        entry = {
            "offer_key": offer_key,
            "resume_id": resume_id,
            "offer_marker": copy.deepcopy(offer_marker),
            "created_at": created_at,
            "updated_at": now_iso,
        }

        if existing:
            self.offer_resume_cache.update(entry, OfferResumeCache.offer_key == offer_key)
        else:
            self.offer_resume_cache.insert(entry)

        return copy.deepcopy(entry)

    def get_offer_cache_statuses(
        self, offer_keys: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Return company-info and resume cache status for the requested offer keys."""
        requested_keys = {
            offer_key.strip() for offer_key in offer_keys if isinstance(offer_key, str) and offer_key.strip()
        }
        if not requested_keys:
            return {}

        statuses: dict[str, dict[str, Any]] = {
            offer_key: {
                "has_company_info": False,
                "resume_id": None,
            }
            for offer_key in requested_keys
        }

        for entry in self.company_info_cache.all():
            offer_key = entry.get("offer_key")
            if offer_key in requested_keys:
                statuses[offer_key]["has_company_info"] = True

        existing_resume_ids = {
            resume.get("resume_id")
            for resume in self.resumes.all()
            if isinstance(resume.get("resume_id"), str)
        }
        stale_offer_keys: list[str] = []

        for entry in self.offer_resume_cache.all():
            offer_key = entry.get("offer_key")
            if offer_key not in requested_keys:
                continue

            resume_id = entry.get("resume_id")
            if not isinstance(resume_id, str) or resume_id not in existing_resume_ids:
                stale_offer_keys.append(offer_key)
                continue

            statuses[offer_key]["resume_id"] = resume_id

        if stale_offer_keys:
            OfferResumeCache = Query()
            for offer_key in stale_offer_keys:
                self.offer_resume_cache.remove(OfferResumeCache.offer_key == offer_key)

        return copy.deepcopy(statuses)

    def delete_offer_resume_cache_by_resume_id(self, resume_id: str) -> int:
        """Remove persistent offer mappings that point to a deleted resume."""
        OfferResumeCache = Query()
        removed = self.offer_resume_cache.remove(OfferResumeCache.resume_id == resume_id)
        return len(removed)

    # Improvement operations
    def create_improvement(
        self,
        original_resume_id: str,
        tailored_resume_id: str,
        job_id: str,
        improvements: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Create an improvement result entry."""
        request_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()

        doc = {
            "request_id": request_id,
            "original_resume_id": original_resume_id,
            "tailored_resume_id": tailored_resume_id,
            "job_id": job_id,
            "improvements": improvements,
            "created_at": now,
        }
        self.improvements.insert(doc)
        return doc

    def get_improvement_by_tailored_resume(
        self, tailored_resume_id: str
    ) -> dict[str, Any] | None:
        """Get improvement record by tailored resume ID.

        This is used to retrieve the job context for on-demand
        cover letter and outreach message generation.
        """
        Improvement = Query()
        result = self.improvements.search(
            Improvement.tailored_resume_id == tailored_resume_id
        )
        return result[0] if result else None

    # Stats
    def get_stats(self) -> dict[str, Any]:
        """Get database statistics."""
        return {
            "total_resumes": len(self.resumes),
            "total_jobs": len(self.jobs),
            "total_improvements": len(self.improvements),
            "has_master_resume": self.get_master_resume() is not None,
        }

    def reset_database(self) -> None:
        """Reset the database by truncating all tables and clearing uploads."""
        # Truncate tables
        self.resumes.truncate()
        self.jobs.truncate()
        self.improvements.truncate()
        self.search_cache.truncate()
        self.company_info_cache.truncate()
        self.offer_resume_cache.truncate()

        # Clear uploads directory
        uploads_dir = settings.data_dir / "uploads"
        if uploads_dir.exists():
            import shutil

            shutil.rmtree(uploads_dir)
            uploads_dir.mkdir(parents=True, exist_ok=True)


# Global database instance
db = Database()

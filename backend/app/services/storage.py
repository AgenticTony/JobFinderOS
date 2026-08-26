"""
CV storage abstraction — backends chosen by STORAGE_BACKEND env.

- local:    disk under uploads/ (default; dev and single-user deploys)
- supabase: Supabase Storage via its OFFICIALLY documented REST API
            (supabase.com/docs/guides/storage/uploads — POST
            /storage/v1/object/{bucket}/{path}, apikey + Bearer service key,
            x-upsert). Vercel Blob was evaluated and rejected for this layer:
            its REST API is undocumented/internal, SDK-only
            (community.vercel.com/t/1136) — not acceptable for production.

Backends return a storage key (relative path). Local reads use it directly;
remote backends return the public object URL as the key.
"""

import logging
import re
from pathlib import Path
from typing import Protocol

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_UPLOADS_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(name: str) -> str:
    """Filesystem/object-safe name (same rule the CV endpoint has always used)."""
    return _SAFE_NAME.sub("-", name).strip("-") or "upload"


class StorageBackend(Protocol):
    def save(self, filename: str, content: bytes, content_type: str) -> str:
        """Persist bytes; returns the storage key (local path or object path)."""
        ...

    def read(self, key: str) -> bytes:
        """Read bytes back from a key (path or object path)."""
        ...



class LocalStorage:
    def save(self, filename: str, content: bytes, content_type: str = "") -> str:
        _UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        path = _UPLOADS_DIR / safe_name(filename)
        path.write_bytes(content)
        return str(path)

    def read(self, key: str) -> bytes:
        return Path(key).read_bytes()


class SupabaseStorage:
    """Official REST: POST {url}/storage/v1/object/{bucket}/{path} with
    apikey + Authorization Bearer (service key), body = raw bytes."""

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": settings.SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
        }

    def save(self, filename: str, content: bytes, content_type: str) -> str:
        if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_KEY:
            raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY not configured")
        object_path = safe_name(filename)
        url = (
            f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1/object/"
            f"{settings.SUPABASE_STORAGE_BUCKET}/{object_path}"
        )
        with httpx.Client(timeout=httpx.Timeout(10, read=60)) as client:
            resp = client.post(
                url,
                headers={**self._headers(), "Content-Type": content_type, "x-upsert": "true"},
                content=content,
            )
            resp.raise_for_status()
        logger.info("Stored %s in Supabase bucket %s", object_path, settings.SUPABASE_STORAGE_BUCKET)
        # Return the OBJECT PATH as the key — never a public URL: CVs are PII,
        # the bucket stays private, and downloads use read() with the service
        # key. A URL here would also break every os.path.exists() consumer.
        return f"{settings.SUPABASE_STORAGE_BUCKET}/{object_path}"

    def read(self, key: str) -> bytes:
        # Keys are "<bucket>/<object path>" — authenticated object GET
        # (supabase.com/docs/reference/api/storage-download)
        bucket, _, object_path = key.partition("/")
        url = f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1/object/{bucket}/{object_path}"
        with httpx.Client(timeout=httpx.Timeout(10, read=60)) as client:
            resp = client.get(url, headers=self._headers())
            resp.raise_for_status()
            return resp.content


def get_storage() -> StorageBackend:
    if settings.STORAGE_BACKEND == "supabase":
        return SupabaseStorage()
    return LocalStorage()


def read_original_cv(profile) -> bytes | None:
    """Original CV bytes through the storage backend, or None if unavailable.
    Storage-aware: works for local paths AND remote object keys."""
    if not profile or not profile.cv_file_path:
        return None
    try:
        return get_storage().read(profile.cv_file_path)
    except Exception as e:  # noqa: BLE001 — missing CV must never kill a send
        logger.warning("Could not read original CV (%s): %s", profile.cv_file_path, e)
        return None

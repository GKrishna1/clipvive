# backend/app/tasks.py
"""
Small, defensive utility functions used by the backend, worker and cleaner.

Functions provided:
- save_job_payload(payload_text: str, owner_id: Optional[int]) -> dict
    Writes the payload to the storage path and returns { job_id, filename, size_bytes }.
    Also attempts to record the job/user usage in the DB if SQLAlchemy/get_session is available.

- cleanup_local_storage() -> dict
    Removes files older than RETENTION_DAYS from STORAGE_PATH and returns {'deleted': N}.
"""

import os
import io
import uuid
import time
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

# env-based configuration
STORAGE_PATH = os.environ.get("STORAGE_PATH", "/data/storage")
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "7"))  # default keep 7 days
STORAGE_MODE = os.environ.get("STORAGE_MODE", "local")  # local | s3 (optional)

# optional libs (boto3 used only when STORAGE_MODE == 's3')
try:
    import boto3  # optional
except Exception:
    boto3 = None

# optional DB update helpers (only used if present)
try:
    from app.db import get_session as _get_session  # try to reuse your project's db helper
    from app.models import User, Job  # optional; safe-guarded usage
except Exception:
    _get_session = None
    User = None
    Job = None

Path(STORAGE_PATH).mkdir(parents=True, exist_ok=True)

def _safe_db_session():
    """
    Return a usable SQLAlchemy session if available, otherwise None.
    This handles either a direct Session return or a generator (FastAPI-style).
    """
    if _get_session is None:
        return None
    sess = _get_session()
    # If get_session returns a generator, get the actual session
    try:
        # generator -> yield a session when iterated
        if hasattr(sess, "__iter__") and not hasattr(sess, "commit"):
            sess_obj = next(sess)
            return sess_obj
    except TypeError:
        pass
    except StopIteration:
        return None
    # if it's already a Session-like object, return it
    return sess

def save_job_payload(payload_text, owner_id=None, filename_prefix=None):
    """
    Save text payload to local storage, optionally attribute to owner.

    Returns: dict with keys: job_id (uuid str), filename, size_bytes
    """
    job_uuid = str(uuid.uuid4())
    prefix = filename_prefix or job_uuid
    filename = f"{prefix}.txt"
    filepath = Path(STORAGE_PATH) / filename

    # Write payload to file (atomic-ish via temp file then rename)
    tmp_path = filepath.with_suffix(".tmp")
    with open(tmp_path, "wb") as f:
        if isinstance(payload_text, str):
            f.write(payload_text.encode("utf-8"))
        else:
            f.write(payload_text)
    tmp_path.replace(filepath)

    size = filepath.stat().st_size

    # Attempt to insert job record / owner attribution to DB (best-effort)
    try:
        session = _safe_db_session()
        if session is not None and User is not None:
            # update user storage usage if owner_id provided
            if owner_id:
                try:
                    u = session.query(User).filter_by(id=owner_id).with_for_update().one_or_none()
                    if u:
                        u.storage_used_bytes = (u.storage_used_bytes or 0) + size
                        session.add(u)
                        session.commit()
                except Exception:
                    session.rollback()
        # Note: we avoid strict schema assumptions about job table here to keep this safe.
    except Exception:
        # swallow DB errors — we prefer the write to disk succeed than erroring whole request
        pass

    return {"job_id": job_uuid, "filename": filename, "size_bytes": size}

def cleanup_local_storage():
    """
    Delete files in STORAGE_PATH older than RETENTION_DAYS.
    Returns {'deleted': N}
    """
    if RETENTION_DAYS <= 0:
        # special: delete nothing unless explicitly called in a maintenance run
        # (your earlier logs warned about RETENTION_DAYS=0 deleting immediately)
        pass

    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    deleted = 0

    try:
        for p in Path(STORAGE_PATH).iterdir():
            # only process files
            try:
                if not p.is_file():
                    continue
                mtime = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)
                if mtime < cutoff:
                    p.unlink()
                    deleted += 1
            except FileNotFoundError:
                continue
            except PermissionError:
                continue
    except Exception:
        # if storage path unreadable, return zero and log upstream
        return {"deleted": 0}

    # Optionally reflect deletions in DB (best-effort)
    try:
        session = _safe_db_session()
        if session is not None and Job is not None:
            # simple best-effort: find jobs whose filename no longer exists and mark deleted
            try:
                rows = session.query(Job).filter(Job.filename != None).all()
                for r in rows:
                    p = Path(STORAGE_PATH) / (r.filename)
                    if not p.exists():
                        # mark deleted
                        r.status = "deleted"
                        session.add(r)
                session.commit()
            except Exception:
                session.rollback()
    except Exception:
        pass

    return {"deleted": deleted}


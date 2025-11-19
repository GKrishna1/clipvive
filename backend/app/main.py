# backend/app/main.py
"""
Minimal FastAPI app file with safe DB session handling and the core endpoints expected
by the frontend and worker:

- GET /health
- POST /auth/register    (simple: create user in DB if available)
- POST /auth/login       (simple token generation placeholder for local dev)
- POST /api/enqueue      (accepts JSON {"text": "..."} and writes to storage; optional Authorization Bearer)
- GET  /api/storage      (returns used_bytes, quota_bytes, plan)
- GET  /api/files        (returns list of files found in STORAGE_PATH)

This module avoids hard crashes if DB or optional packages are missing.
"""

import os
import uuid
from typing import Optional
from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, ValidationError

# import local helpers
from app.tasks import save_job_payload
# optional DB/get_session helpers
try:
    from app.db import get_session as _get_session
    from app.models import User, Job
except Exception:
    _get_session = None
    User = None
    Job = None

# Ensure storage path exists
STORAGE_PATH = os.environ.get("STORAGE_PATH", "/data/storage")
os.makedirs(STORAGE_PATH, exist_ok=True)

app = FastAPI(title="clipvive-api")

class RegisterIn(BaseModel):
    email: EmailStr
    password: str

class LoginIn(BaseModel):
    email: EmailStr
    password: str
    device_id: Optional[str] = None
    device_type: Optional[str] = None

class EnqueueIn(BaseModel):
    text: str

def _safe_db_session():
    if _get_session is None:
        return None
    sess = _get_session()
    try:
        if hasattr(sess, "__iter__") and not hasattr(sess, "commit"):
            ses = next(sess)
            return ses
    except StopIteration:
        return None
    return sess

@app.get("/health")
def health():
    return {"status": "ok", "service": "clipvive-api"}

@app.post("/auth/register")
def register(payload: RegisterIn):
    """
    Simple registration: best-effort create user in DB if DB exists.
    Returns 201-ish payload; if DB unavailable, returns helpful error.
    """
    try:
        session = _safe_db_session()
        if session is None or User is None:
            # No DB available — return 503 so callers know registration isn't set up
            raise HTTPException(status_code=503, detail="database unavailable")
        # check existing
        existing = session.query(User).filter_by(email=payload.email).one_or_none()
        if existing:
            return JSONResponse({"detail": "user exists"}, status_code=409)
        u = User(email=payload.email)
        # NOTE: store hashed password properly in real app. Here we mimic existing flows.
        u.hashed_password = payload.password[:72]  # bcrypt limit guard; real app must hash
        session.add(u)
        session.commit()
        return {"id": u.id, "email": u.email}
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.errors())
    except HTTPException:
        raise
    except Exception as e:
        # avoid exposing internals
        return JSONResponse({"detail": "Internal Server Error"}, status_code=500)

@app.post("/auth/login")
def login(payload: LoginIn):
    """
    Simple login stub that returns a fake JWT-ish token for development.
    In production use proper JWT signing & password verification.
    """
    # For now: verify user exists if DB present; otherwise allow a dev login.
    try:
        session = _safe_db_session()
        if session is not None and User is not None:
            user = session.query(User).filter_by(email=payload.email).one_or_none()
            if user is None:
                raise HTTPException(status_code=401, detail="invalid credentials")
            # NOTE: do real password check in production
            user_id = user.id
        else:
            # local dev fallback: return a token with a random jti
            user_id = 1
        # return a simple token (not a real JWT)
        token = f"devtoken-{uuid.uuid4().hex}"
        return {"access_token": token, "token_type": "bearer", "user_id": user_id}
    except HTTPException:
        raise
    except Exception:
        return JSONResponse({"detail": "Internal Server Error"}, status_code=500)

def _get_owner_from_auth(authorization: Optional[str]):
    """
    If caller provided a Bearer token, try to infer owner_id. This is intentionally
    minimal: it will only decode the simple dev token above (or try DB if implemented).
    """
    if not authorization:
        return None
    if authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
        # dev token format: devtoken-<hex>
        if token.startswith("devtoken-"):
            # return a placeholder owner id — real logic: decode JWT & return subject
            return 1
    return None

@app.post("/api/enqueue")
def api_enqueue(req: EnqueueIn, Authorization: Optional[str] = Header(None)):
    """
    Accepts {"text":"..."} and writes it to STORAGE_PATH via tasks.save_job_payload.
    Returns enqueued:true and job_id. If DB is available, owner attribution updates user usage.
    """
    owner_id = _get_owner_from_auth(Authorization)
    try:
        res = save_job_payload(req.text, owner_id=owner_id)
        # In a full deploy you'd now push an RQ job or similar. Worker reads files directly.
        return {"enqueued": True, "job_id": res["job_id"], "rq_id": res["job_id"]}
    except Exception:
        return JSONResponse({"detail": "Internal Server Error"}, status_code=500)

@app.get("/api/storage")
def api_storage(Authorization: Optional[str] = Header(None)):
    """
    Return storage usage. We try DB if available, otherwise return defaults (free plan).
    """
    quota_bytes = int(os.environ.get("DEFAULT_QUOTA_BYTES", 500 * 1024 * 1024))  # 500MB default
    plan_name = os.environ.get("DEFAULT_PLAN", "free")
    owner_id = _get_owner_from_auth(Authorization)
    used_bytes = 0
    try:
        session = _safe_db_session()
        if session is not None and User is not None and owner_id:
            u = session.query(User).filter_by(id=owner_id).one_or_none()
            if u:
                used_bytes = getattr(u, "storage_used_bytes", 0) or 0
        else:
            # Fallback: compute from disk (best-effort)
            import os
            total = 0
            for p in os.scandir(STORAGE_PATH):
                if p.is_file():
                    try:
                        total += p.stat().st_size
                    except Exception:
                        pass
            used_bytes = total
        return {"used_bytes": int(used_bytes), "quota_bytes": quota_bytes, "plan": plan_name}
    except Exception:
        return JSONResponse({"detail": "Internal Server Error"}, status_code=500)

@app.get("/api/files")
def api_files(Authorization: Optional[str] = Header(None)):
    """
    Return list of files in STORAGE_PATH (filename + size + created_at).
    Keeps the response small and safe for the UI.
    """
    try:
        files = []
        import os
        from datetime import datetime
        for entry in os.scandir(STORAGE_PATH):
            if not entry.is_file():
                continue
            st = entry.stat()
            files.append({
                "filename": entry.name,
                "size": st.st_size,
                "created_at": datetime.utcfromtimestamp(st.st_ctime).isoformat() + "Z"
            })
        return {"files": files}
    except Exception:
        return JSONResponse({"detail": "Internal Server Error"}, status_code=500)


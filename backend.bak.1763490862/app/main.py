from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
import os
import uuid
from redis import Redis
from rq import Queue
from typing import Dict, List

from .db import init_db, get_session
from .auth import router as auth_router, get_current_user
from .models import User, Job
from sqlmodel import select
from sqlmodel import Session as SQLSession
from .tasks import cleanup_local_storage
from datetime import datetime

app = FastAPI(title="clipvive-api")

@app.on_event("startup")
def on_startup():
    init_db()

app.include_router(auth_router, prefix="/auth")

@app.get("/health")
async def health():
    return {"status":"ok","service":"clipvive-api"}

class EchoIn(BaseModel):
    text: str

@app.post("/api/echo")
async def echo(payload: EchoIn):
    return {"echo": payload.text}

# storage info endpoint
@app.get("/api/storage")
def storage_info(user: User = Depends(get_current_user), session: SQLSession = Depends(get_session)):
    plan = user.plan or "free"
    quotas = {
        "free": int(os.getenv("PLAN_FREE_BYTES", "524288000")),
        "basic": int(os.getenv("PLAN_BASIC_BYTES", "5368709120")),
        "pro": int(os.getenv("PLAN_PRO_BYTES", "21474836480")),
    }
    quota = quotas.get(plan, quotas["free"])
    return {"used_bytes": user.storage_used_bytes, "quota_bytes": quota, "plan": plan}

# files listing - properly use session dependency
@app.get("/api/files")
def list_files(user: User = Depends(get_current_user), session: SQLSession = Depends(get_session)):
    q = session.exec(select(Job).where(Job.owner_id == user.id))
    jobs = q.all()
    out = []
    for j in jobs:
        out.append({
            "job_id": j.job_id,
            "filename": j.filename,
            "size_bytes": j.size_bytes,
            "status": j.status,
            "created_at": j.created_at.isoformat() if j.created_at else None,
            "processed_at": j.processed_at.isoformat() if j.processed_at else None
        })
    return {"files": out}

# delete a file (owner only) - uses session dependency
@app.delete("/api/files/{job_id}")
def delete_file(job_id: str, user: User = Depends(get_current_user), session: SQLSession = Depends(get_session)):
    job = session.get(Job, job_id)
    if not job or job.owner_id != user.id:
        raise HTTPException(status_code=404, detail="File not found")
    try:
        if job.filename and os.path.isfile(job.filename):
            size = os.path.getsize(job.filename)
            os.remove(job.filename)
            # decrement user storage
            session.execute("UPDATE \"user\" SET storage_used_bytes = GREATEST(COALESCE(storage_used_bytes,0) - :d,0) WHERE id = :uid", {"d": size, "uid": user.id})
        job.status = "deleted"
        session.add(job)
        session.commit()
    except Exception:
        raise HTTPException(status_code=500, detail="Could not delete file")
    return {"deleted": True, "job_id": job_id}

# enqueue input
class EnqueueIn(BaseModel):
    text: str

# build Redis/RQ queue
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_conn = Redis.from_url(REDIS_URL)
q = Queue("default", connection=redis_conn)

@app.post("/api/enqueue")
def enqueue(payload: EnqueueIn, user: User = Depends(get_current_user), session: SQLSession = Depends(get_session)) -> Dict:
    estimated_size = max(len(payload.text.encode('utf-8')) + 1024, 1024)
    plan = user.plan or "free"
    quotas = {
        "free": int(os.getenv("PLAN_FREE_BYTES", "524288000")),
        "basic": int(os.getenv("PLAN_BASIC_BYTES", "5368709120")),
        "pro": int(os.getenv("PLAN_PRO_BYTES", "21474836480")),
    }
    quota = quotas.get(plan, quotas["free"])
    if (user.storage_used_bytes or 0) + estimated_size > quota:
        raise HTTPException(status_code=403, detail="Enqueue would exceed your storage quota. Consider upgrading plan.")
    job_id = str(uuid.uuid4())
    from .tasks import process_task
    job = q.enqueue(process_task, payload.text, job_id, user.id, job_id=job_id, result_ttl=5000)
    return {"enqueued": True, "job_id": job_id, "rq_id": job.id}

# example protected route
@app.get("/api/me")
def me(user=Depends(get_current_user)):
    return {"id": user.id, "email": user.email}

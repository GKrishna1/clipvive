import time
import os
from datetime import datetime, timezone
import boto3
from botocore.client import Config
from sqlmodel import select, Session
from .db import engine, get_session
from .models import User, Job
from sqlalchemy import text

STORAGE_DIR = os.getenv("OUT_DIR", "/data/storage")

# S3 config (optional)
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "")
S3_REGION = os.getenv("S3_REGION", "")
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "")
REMOVE_LOCAL_AFTER_UPLOAD = os.getenv("REMOVE_LOCAL_AFTER_UPLOAD", "false").lower() in ("1","true","yes")
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "7"))

def _s3_client():
    if not (S3_ENDPOINT and S3_ACCESS_KEY and S3_SECRET_KEY and S3_BUCKET):
        return None
    session = boto3.session.Session()
    return session.client(
        's3',
        region_name=S3_REGION or None,
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        config=Config(signature_version='s3v4'),
    )

def upload_to_s3(local_path: str, object_name: str) -> dict:
    s3 = _s3_client()
    if not s3:
        return {"uploaded": False, "reason": "no_s3_config"}
    try:
        s3.upload_file(local_path, S3_BUCKET, object_name)
        url = f"{S3_ENDPOINT.rstrip('/')}/{S3_BUCKET}/{object_name}"
        return {"uploaded": True, "url": url}
    except Exception as e:
        return {"uploaded": False, "reason": str(e)}

def _inc_user_storage(user_id: int, delta: int):
    with engine.connect() as conn:
        conn.execute(text("UPDATE \"user\" SET storage_used_bytes = COALESCE(storage_used_bytes,0) + :d WHERE id = :uid"), {"d": delta, "uid": user_id})
        conn.commit()

def _dec_user_storage(user_id: int, delta: int):
    with engine.connect() as conn:
        conn.execute(text("UPDATE \"user\" SET storage_used_bytes = GREATEST(COALESCE(storage_used_bytes,0) - :d, 0) WHERE id = :uid"), {"d": delta, "uid": user_id})
        conn.commit()

def process_task(text: str, job_id: str, owner_id: int = None):
    """Write a local file (always). Create/Update Job record, update user's storage account. If S3 configured try uploading. Remove local if configured and upload succeeded."""
    os.makedirs(STORAGE_DIR, exist_ok=True)
    filename = os.path.join(STORAGE_DIR, f"{job_id}.txt")

    # create job record in DB as 'processing'
    try:
        with Session(engine) as session:
            job = Job(job_id=job_id, owner_id=owner_id, filename=filename, status="processing", created_at=datetime.utcnow())
            session.add(job)
            session.commit()
    except Exception:
        pass

    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"job_id: {job_id}\n")
        f.write(f"timestamp: {datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()}\n\n")
        f.write(text + "\n")
    # simulate work
    time.sleep(1)

    # get file size
    try:
        size = os.path.getsize(filename)
    except Exception:
        size = 0

    # if owner_id provided, increment storage atomically and update job size
    if owner_id:
        _inc_user_storage(owner_id, size)
        try:
            with Session(engine) as session:
                j = session.get(Job, job_id)
                if j:
                    j.size_bytes = size
                    j.status = "done"
                    j.processed_at = datetime.utcnow()
                    session.add(j)
                    session.commit()
        except Exception:
            pass

    # attempt upload to S3 (optional)
    s3_obj = f"outputs/{job_id}.txt"
    upload_result = upload_to_s3(filename, s3_obj)

    # if upload succeeded and removal configured, delete local file (do not change total storage count)
    if upload_result.get("uploaded") and REMOVE_LOCAL_AFTER_UPLOAD:
        try:
            os.remove(filename)
        except Exception:
            pass

    return {"job_id": job_id, "local_path": filename, "s3": upload_result}

# housekeeping: owner-aware cleanup
def cleanup_local_storage():
    try:
        cutoff_ts = datetime.utcnow().timestamp() - (RETENTION_DAYS * 86400)
        if not os.path.isdir(STORAGE_DIR):
            return {"deleted": 0, "reason": "no_dir"}
        deleted = 0
        # iterate jobs with status done and filename exists and older than cutoff
        with Session(engine) as session:
            q = session.exec(select(Job).where(Job.status == "done"))
            jobs = q.all()
            for job in jobs:
                try:
                    if not job.filename:
                        continue
                    if not os.path.isfile(job.filename):
                        continue
                    mtime = os.path.getmtime(job.filename)
                    if mtime < cutoff_ts:
                        size = os.path.getsize(job.filename)
                        os.remove(job.filename)
                        deleted += 1
                        # decrement owner's storage
                        if job.owner_id:
                            _dec_user_storage(job.owner_id, size)
                        # mark job deleted
                        job.status = "deleted"
                        session.add(job)
                        session.commit()
                except Exception:
                    continue
        return {"deleted": deleted}
    except Exception as e:
        return {"error": str(e)}

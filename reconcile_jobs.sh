#!/bin/bash
# Reconcile disk files with database jobs + recompute user storage

set -e

echo "=== Clipvive: Job & Storage Reconciliation ==="
echo "Running inside backend container..."

docker exec -i clipvive-backend-1 python - <<'PY'
import os, time
from pathlib import Path
from sqlmodel import select
from sqlmodel import Session
from app.db import engine
from app.models import Job, User
from datetime import datetime

STORAGE_DIR = "/data/storage"  # inside container

created = 0
skipped = 0
errors = 0

print("Scanning storage:", STORAGE_DIR)

with Session(engine) as session:
    # Default owner (modify if needed)
    default_owner = session.exec(select(User).where(User.id == 1)).first()
    owner_id_default = default_owner.id if default_owner else None

    for p in Path(STORAGE_DIR).glob("*.txt"):
        try:
            job_id = p.stem
            existing = session.get(Job, job_id)
            if existing:
                skipped += 1
                continue

            size = p.stat().st_size
            mtime = p.stat().st_mtime
            created_at = datetime.utcfromtimestamp(mtime)

            job = Job(
                job_id=job_id,
                owner_id=owner_id_default,
                filename=str(p),
                size_bytes=size,
                status="done",
                created_at=created_at,
                processed_at=datetime.utcnow()
            )
            session.add(job)
            session.commit()
            created += 1

        except Exception as e:
            print("ERROR on", p, ":", e)
            errors += 1
            session.rollback()

    # Recompute per-user storage totals
    print("Recomputing user storage totals...")
    users = session.exec(select(User)).all()
    for u in users:
        jobs = session.exec(select(Job).where(Job.owner_id == u.id)).all()
        total = sum((j.size_bytes or 0) for j in jobs)
        session.execute(
            "UPDATE \"user\" SET storage_used_bytes = :s WHERE id = :uid",
            {"s": total, "uid": u.id}
        )
    session.commit()

print("=== Reconciliation Complete ===")
print("Created jobs:", created)
print("Skipped existing:", skipped)
print("Errors:", errors)
PY

echo "Done."

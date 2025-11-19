from sqlmodel import SQLModel, Field
from typing import Optional
from datetime import datetime

class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, sa_column_kwargs={"unique": True})
    hashed_password: str
    plan: str = Field(default="free")  # free/basic/pro
    storage_used_bytes: int = Field(default=0)  # total bytes used by user
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Session(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    device_id: str
    device_type: str  # 'browser' or 'mobile'
    jti: str  # JWT unique id
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Job(SQLModel, table=True):
    job_id: str = Field(primary_key=True, index=True)
    owner_id: Optional[int] = Field(default=None, index=True)
    filename: Optional[str] = None
    size_bytes: Optional[int] = 0
    status: str = Field(default="created")  # created/processing/done/deleted/failed
    created_at: datetime = Field(default_factory=datetime.utcnow)
    processed_at: Optional[datetime] = None

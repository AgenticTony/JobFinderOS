"""WO-31: status of one queued AI job — the poll behind every
"you're in a queue" message (suggest results ride here too)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_authenticated_user, owns_or_404
from app.core.database import get_db
from app.models import AIJob, User
from app.services import ai_queue

router = APIRouter()


@router.get("/{job_id}")
async def get_ai_job(
    job_id: int, db: Session = Depends(get_db), user: User = Depends(get_authenticated_user)
):
    """{id, kind, status, position, eta_minutes, paused_until, error,
    result?} — the result only for the caller's own finished job."""
    job = db.get(AIJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    owns_or_404(job.user_id, user, "Job")
    return ai_queue.job_view(job, include_result=True)

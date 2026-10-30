"""Background job status and backups."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database.db import get_session
from services import backup, system_status

router = APIRouter(prefix="/api/system")


@router.get("/status")
def status(session: Session = Depends(get_session)) -> dict:
    """When the auto-update last ran and what it did, Odds API credits left, the last backup, data freshness."""
    return system_status.status(session)


@router.post("/backup")
def backup_now(session: Session = Depends(get_session)) -> dict:
    """Make a database backup now (the auto-update also makes one a day)."""
    result = backup.backup_now(session)
    if not result["ok"]:
        raise HTTPException(500, f"Backup failed ({result['error']}).")
    return result

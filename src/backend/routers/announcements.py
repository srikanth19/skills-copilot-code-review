"""
Announcement endpoints for the High School Management System API
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..database import announcements_collection, teachers_collection

router = APIRouter(
    prefix="/announcements",
    tags=["announcements"]
)


class AnnouncementPayload(BaseModel):
    """Request payload for creating/updating an announcement."""

    message: str = Field(..., min_length=1, max_length=400)
    expires_at: str
    starts_at: Optional[str] = None


def _parse_datetime(date_str: Optional[str], field_name: str, required: bool) -> Optional[datetime]:
    """Parse ISO datetime strings with optional Z suffix into UTC-naive datetime objects."""
    if not date_str:
        if required:
            raise HTTPException(status_code=400, detail=f"{field_name} is required")
        return None

    try:
        normalized = date_str.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        # Store as naive UTC for consistent Mongo comparisons.
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must be a valid ISO datetime"
        ) from exc


def _require_authenticated_teacher(teacher_username: Optional[str]) -> Dict[str, Any]:
    """Require teacher username and return teacher document when valid."""
    if not teacher_username:
        raise HTTPException(status_code=401, detail="Authentication required for this action")

    teacher = teachers_collection.find_one({"_id": teacher_username})
    if not teacher:
        raise HTTPException(status_code=401, detail="Invalid teacher credentials")

    return teacher


def _serialize_announcement(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Convert MongoDB document into API-safe response payload."""
    starts_at = doc.get("starts_at")
    expires_at = doc.get("expires_at")

    if isinstance(starts_at, datetime):
        starts_at = starts_at.isoformat()
    if isinstance(expires_at, datetime):
        expires_at = expires_at.isoformat()

    return {
        "id": str(doc["_id"]),
        "message": doc.get("message", ""),
        "starts_at": starts_at,
        "expires_at": expires_at,
    }


def _coerce_datetime(value: Any) -> Optional[datetime]:
    """Convert stored datetime or ISO string into naive UTC datetime for comparisons."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        except ValueError:
            return None
    return None


@router.get("", response_model=List[Dict[str, Any]])
def get_active_announcements() -> List[Dict[str, Any]]:
    """Get currently active announcements for public display."""
    now = datetime.utcnow()
    docs = list(announcements_collection.find({}))
    active_docs = []

    for doc in docs:
        starts_at = _coerce_datetime(doc.get("starts_at"))
        expires_at = _coerce_datetime(doc.get("expires_at"))
        if not expires_at:
            continue
        if starts_at and starts_at > now:
            continue
        if expires_at < now:
            continue
        active_docs.append(doc)

    active_docs.sort(key=lambda doc: _coerce_datetime(doc.get("expires_at")) or datetime.max)
    return [_serialize_announcement(doc) for doc in active_docs]


@router.get("/manage", response_model=List[Dict[str, Any]])
def get_all_announcements_for_management(
    teacher_username: Optional[str] = Query(None)
) -> List[Dict[str, Any]]:
    """Get all announcements (including future/expired) for signed-in users."""
    _require_authenticated_teacher(teacher_username)

    docs = list(announcements_collection.find({}))
    docs.sort(key=lambda doc: _coerce_datetime(doc.get("expires_at")) or datetime.max)
    return [_serialize_announcement(doc) for doc in docs]


@router.post("/manage", response_model=Dict[str, Any])
def create_announcement(
    payload: AnnouncementPayload,
    teacher_username: Optional[str] = Query(None)
) -> Dict[str, Any]:
    """Create a new announcement. Requires teacher authentication."""
    _require_authenticated_teacher(teacher_username)

    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message cannot be empty")

    starts_at = _parse_datetime(payload.starts_at, "starts_at", required=False)
    expires_at = _parse_datetime(payload.expires_at, "expires_at", required=True)

    if starts_at and expires_at <= starts_at:
        raise HTTPException(
            status_code=400,
            detail="expires_at must be later than starts_at"
        )

    doc = {
        "message": message,
        "starts_at": starts_at,
        "expires_at": expires_at,
    }
    result = announcements_collection.insert_one(doc)
    created = announcements_collection.find_one({"_id": result.inserted_id})

    if not created:
        raise HTTPException(status_code=500, detail="Failed to create announcement")

    return _serialize_announcement(created)


@router.put("/manage/{announcement_id}", response_model=Dict[str, Any])
def update_announcement(
    announcement_id: str,
    payload: AnnouncementPayload,
    teacher_username: Optional[str] = Query(None)
) -> Dict[str, Any]:
    """Update an announcement by ID. Requires teacher authentication."""
    _require_authenticated_teacher(teacher_username)

    try:
        mongo_id = ObjectId(announcement_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid announcement id") from exc

    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message cannot be empty")

    starts_at = _parse_datetime(payload.starts_at, "starts_at", required=False)
    expires_at = _parse_datetime(payload.expires_at, "expires_at", required=True)

    if starts_at and expires_at <= starts_at:
        raise HTTPException(
            status_code=400,
            detail="expires_at must be later than starts_at"
        )

    result = announcements_collection.update_one(
        {"_id": mongo_id},
        {
            "$set": {
                "message": message,
                "starts_at": starts_at,
                "expires_at": expires_at,
            }
        }
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Announcement not found")

    updated = announcements_collection.find_one({"_id": mongo_id})
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to load announcement")

    return _serialize_announcement(updated)


@router.delete("/manage/{announcement_id}", response_model=Dict[str, Any])
def delete_announcement(
    announcement_id: str,
    teacher_username: Optional[str] = Query(None)
) -> Dict[str, Any]:
    """Delete an announcement by ID. Requires teacher authentication."""
    _require_authenticated_teacher(teacher_username)

    try:
        mongo_id = ObjectId(announcement_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid announcement id") from exc

    result = announcements_collection.delete_one({"_id": mongo_id})

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Announcement not found")

    return {"message": "Announcement deleted"}

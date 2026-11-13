from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from autoescrow.models.identity import RefreshSession


class SessionResponse(BaseModel):
    id: UUID
    created_at: datetime
    expires_at: datetime
    current: bool

    @classmethod
    def from_session(cls, session: RefreshSession, *, current: bool) -> "SessionResponse":
        return cls(
            id=session.id,
            created_at=session.created_at,
            expires_at=session.expires_at,
            current=current,
        )


class SessionListResponse(BaseModel):
    sessions: list[SessionResponse]

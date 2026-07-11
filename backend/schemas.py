from datetime import datetime

from pydantic import BaseModel

from models import MessageRole, MissionStatus


class MissionCreate(BaseModel):
    titre: str
    objectif: str | None = None


class MissionOut(BaseModel):
    id: int
    titre: str
    objectif: str | None
    statut: MissionStatus
    date_creation: datetime

    class Config:
        from_attributes = True


class MessageCreate(BaseModel):
    role: MessageRole
    contenu: str


class MessageOut(BaseModel):
    id: int
    mission_id: int
    role: MessageRole
    contenu: str
    date_creation: datetime

    class Config:
        from_attributes = True


class MissionDetailOut(MissionOut):
    messages: list[MessageOut] = []


class ChatMessageIn(BaseModel):
    contenu: str


class ChatResponse(BaseModel):
    id: int
    mission_id: int
    role: MessageRole
    contenu: str
    date_creation: datetime
    discovery_complete: bool

    class Config:
        from_attributes = True

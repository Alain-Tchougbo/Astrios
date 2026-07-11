from datetime import datetime

from pydantic import BaseModel

from models import ActionStatus, MessageRole, MissionStatus, TaskStatus


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
    plan_generated: bool = False
    tasks_created: int = 0
    documents_generated: bool = False
    documents_created: int = 0
    action_proposed: bool = False

    class Config:
        from_attributes = True


class TaskOut(BaseModel):
    id: int
    mission_id: int
    titre: str
    description: str | None
    ordre: int
    statut: TaskStatus
    date_creation: datetime

    class Config:
        from_attributes = True


class DocumentOut(BaseModel):
    id: int
    mission_id: int
    titre: str
    type: str
    contenu: str
    date_creation: datetime

    class Config:
        from_attributes = True


class ActionOut(BaseModel):
    id: int
    mission_id: int
    type: str
    destinataire: str
    sujet: str
    contenu: str
    statut: ActionStatus
    date_creation: datetime

    class Config:
        from_attributes = True


class ActionApprovalResponse(BaseModel):
    action: ActionOut
    simulated: bool
    message: str


class RetryResponse(BaseModel):
    plan_generated: bool = False
    tasks_created: int = 0
    documents_generated: bool = False
    documents_created: int = 0
    action_proposed: bool = False

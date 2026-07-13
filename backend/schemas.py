from datetime import datetime

from pydantic import BaseModel

from models import ActionStatus, MessageRole, MissionStatus, TaskStatus


class MissionCreate(BaseModel):
    titre: str
    objectif: str | None = None


class MissionUpdate(BaseModel):
    titre: str


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
    actions_created: int = 0
    suggestions: list[str] = []

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


class DocumentUpdate(BaseModel):
    contenu: str


class ActionOut(BaseModel):
    id: int
    mission_id: int
    task_id: int | None = None
    type: str
    destinataire: str
    sujet: str
    contenu: str
    details: dict | None = None
    statut: ActionStatus
    date_creation: datetime

    class Config:
        from_attributes = True


class ActionEmailUpdate(BaseModel):
    destinataire: str
    sujet: str
    contenu: str


class ActionApprovalResponse(BaseModel):
    action: ActionOut
    message: str


class ActionExecutionResult(BaseModel):
    action: ActionOut
    success: bool
    message: str


class ApproveAllResponse(BaseModel):
    results: list[ActionExecutionResult]
    mission_statut: MissionStatus


class RetryResponse(BaseModel):
    plan_generated: bool = False
    tasks_created: int = 0
    documents_generated: bool = False
    documents_created: int = 0
    action_proposed: bool = False
    actions_created: int = 0

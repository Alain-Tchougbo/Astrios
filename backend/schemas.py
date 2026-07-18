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
    suggestions: list[str] = []

    class Config:
        from_attributes = True


class GeneratePlanResponse(BaseModel):
    tasks_created: int


class GenerateDocumentsResponse(BaseModel):
    documents_created: int


class GenerateActionsResponse(BaseModel):
    actions_created: int
    action_proposed: bool


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
    purpose: str | None = None
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
    execution_mode: str | None = None
    date_creation: datetime

    class Config:
        from_attributes = True


class ActionUpdate(BaseModel):
    # Email
    destinataire: str | None = None
    sujet: str | None = None
    contenu: str | None = None
    # Calendar event
    titre: str | None = None
    description: str | None = None
    date_debut: str | None = None
    date_fin: str | None = None
    participants: str | None = None


class ActionApprovalResponse(BaseModel):
    action: ActionOut
    message: str
    mode: str


class ActionExecutionResult(BaseModel):
    action: ActionOut
    success: bool
    message: str


class ApproveAllResponse(BaseModel):
    results: list[ActionExecutionResult]
    mission_statut: MissionStatus
    mode: str


class RetryResponse(BaseModel):
    plan_generated: bool = False
    tasks_created: int = 0
    documents_generated: bool = False
    documents_created: int = 0
    action_proposed: bool = False
    actions_created: int = 0

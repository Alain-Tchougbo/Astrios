import enum
from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from database import Base


class MissionStatus(str, enum.Enum):
    nouvelle = "nouvelle"
    en_cours = "en_cours"
    plan_pret = "plan_pret"
    documents_prets = "documents_prets"
    action_en_attente = "action_en_attente"
    terminee = "terminee"


class MessageRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"


class TaskStatus(str, enum.Enum):
    a_faire = "a_faire"
    terminee = "terminee"


class ActionStatus(str, enum.Enum):
    en_attente = "en_attente"
    approuvee = "approuvee"
    rejetee = "rejetee"
    executee = "executee"


class Mission(Base):
    __tablename__ = "missions"

    id = Column(Integer, primary_key=True, index=True)
    titre = Column(String, nullable=False)
    objectif = Column(Text, nullable=True)
    statut = Column(Enum(MissionStatus), nullable=False, default=MissionStatus.nouvelle)
    date_creation = Column(DateTime, default=datetime.utcnow)

    messages = relationship("Message", back_populates="mission", cascade="all, delete-orphan")
    tasks = relationship(
        "Task", back_populates="mission", cascade="all, delete-orphan", order_by="Task.ordre"
    )
    documents = relationship(
        "Document", back_populates="mission", cascade="all, delete-orphan", order_by="Document.id"
    )
    actions = relationship(
        "Action", back_populates="mission", cascade="all, delete-orphan", order_by="Action.id"
    )


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    mission_id = Column(Integer, ForeignKey("missions.id"), nullable=False)
    role = Column(Enum(MessageRole), nullable=False)
    contenu = Column(Text, nullable=False)
    date_creation = Column(DateTime, default=datetime.utcnow)

    mission = relationship("Mission", back_populates="messages")


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    mission_id = Column(Integer, ForeignKey("missions.id"), nullable=False)
    titre = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    ordre = Column(Integer, nullable=False, default=0)
    statut = Column(Enum(TaskStatus), nullable=False, default=TaskStatus.a_faire)
    date_creation = Column(DateTime, default=datetime.utcnow)

    mission = relationship("Mission", back_populates="tasks")


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    mission_id = Column(Integer, ForeignKey("missions.id"), nullable=False)
    titre = Column(String, nullable=False)
    type = Column(String, nullable=False)
    contenu = Column(Text, nullable=False)
    date_creation = Column(DateTime, default=datetime.utcnow)

    mission = relationship("Mission", back_populates="documents")


class Action(Base):
    __tablename__ = "actions"

    id = Column(Integer, primary_key=True, index=True)
    mission_id = Column(Integer, ForeignKey("missions.id"), nullable=False)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True)
    type = Column(String, nullable=False)
    destinataire = Column(String, nullable=False, default="")
    sujet = Column(String, nullable=False, default="")
    contenu = Column(Text, nullable=False, default="")
    details = Column(JSON, nullable=True)
    statut = Column(Enum(ActionStatus), nullable=False, default=ActionStatus.en_attente)
    date_creation = Column(DateTime, default=datetime.utcnow)

    mission = relationship("Mission", back_populates="actions")

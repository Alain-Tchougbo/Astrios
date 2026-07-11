import enum
from datetime import datetime

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from database import Base


class MissionStatus(str, enum.Enum):
    nouvelle = "nouvelle"
    en_cours = "en_cours"
    plan_pret = "plan_pret"
    terminee = "terminee"


class MessageRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"


class TaskStatus(str, enum.Enum):
    a_faire = "a_faire"
    terminee = "terminee"


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

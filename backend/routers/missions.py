from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import models
import schemas
from database import get_db
from orion import DISCOVERY_COMPLETE_TAG, ask_orion

router = APIRouter(prefix="/missions", tags=["missions"])


@router.post("", response_model=schemas.MissionOut)
def create_mission(mission: schemas.MissionCreate, db: Session = Depends(get_db)):
    db_mission = models.Mission(titre=mission.titre, objectif=mission.objectif)
    db.add(db_mission)
    db.commit()
    db.refresh(db_mission)
    return db_mission


@router.get("", response_model=list[schemas.MissionOut])
def list_missions(db: Session = Depends(get_db)):
    return db.query(models.Mission).order_by(models.Mission.date_creation.desc()).all()


@router.get("/{mission_id}", response_model=schemas.MissionDetailOut)
def get_mission(mission_id: int, db: Session = Depends(get_db)):
    mission = db.query(models.Mission).filter(models.Mission.id == mission_id).first()
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable")
    return mission


@router.get("/{mission_id}/messages", response_model=list[schemas.MessageOut])
def list_messages(mission_id: int, db: Session = Depends(get_db)):
    mission = db.query(models.Mission).filter(models.Mission.id == mission_id).first()
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable")
    return (
        db.query(models.Message)
        .filter(models.Message.mission_id == mission_id)
        .order_by(models.Message.date_creation)
        .all()
    )


@router.post("/{mission_id}/chat", response_model=schemas.ChatResponse)
def chat_with_orion(mission_id: int, payload: schemas.ChatMessageIn, db: Session = Depends(get_db)):
    mission = db.query(models.Mission).filter(models.Mission.id == mission_id).first()
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable")

    user_message = models.Message(
        mission_id=mission_id, role=models.MessageRole.user, contenu=payload.contenu
    )
    db.add(user_message)
    db.commit()

    history = (
        db.query(models.Message)
        .filter(models.Message.mission_id == mission_id)
        .order_by(models.Message.date_creation)
        .all()
    )
    qwen_history = [{"role": m.role.value, "content": m.contenu} for m in history]

    raw_reply = ask_orion(qwen_history)
    discovery_complete = DISCOVERY_COMPLETE_TAG in raw_reply
    clean_reply = raw_reply.replace(DISCOVERY_COMPLETE_TAG, "").strip()

    assistant_message = models.Message(
        mission_id=mission_id, role=models.MessageRole.assistant, contenu=clean_reply
    )
    db.add(assistant_message)

    if discovery_complete and mission.statut == models.MissionStatus.nouvelle:
        mission.statut = models.MissionStatus.en_cours

    db.commit()
    db.refresh(assistant_message)

    return schemas.ChatResponse(
        id=assistant_message.id,
        mission_id=assistant_message.mission_id,
        role=assistant_message.role,
        contenu=assistant_message.contenu,
        date_creation=assistant_message.date_creation,
        discovery_complete=discovery_complete,
    )

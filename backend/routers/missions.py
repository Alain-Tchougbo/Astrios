from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import models
import schemas
from database import get_db
from email_sender import EmailSendError, is_email_configured, send_email
from orion import (
    DISCOVERY_COMPLETE_TAG,
    OrionAPIError,
    ask_orion,
    generate_documents,
    generate_plan,
    propose_action,
)

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

    try:
        raw_reply = ask_orion(qwen_history)
    except OrionAPIError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    discovery_complete = DISCOVERY_COMPLETE_TAG in raw_reply
    clean_reply = raw_reply.replace(DISCOVERY_COMPLETE_TAG, "").strip()

    assistant_message = models.Message(
        mission_id=mission_id, role=models.MessageRole.assistant, contenu=clean_reply
    )
    db.add(assistant_message)
    db.commit()
    db.refresh(assistant_message)

    plan_generated = False
    tasks_created = 0
    documents_generated = False
    documents_created = 0
    action_proposed = False

    if discovery_complete and mission.statut == models.MissionStatus.nouvelle:
        mission.statut = models.MissionStatus.en_cours

        try:
            plan_tasks = generate_plan(mission.objectif, clean_reply)
        except OrionAPIError as exc:
            db.commit()
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        for ordre, task_data in enumerate(plan_tasks):
            db.add(
                models.Task(
                    mission_id=mission_id,
                    titre=task_data["titre"],
                    description=task_data.get("description"),
                    ordre=ordre,
                )
            )
        tasks_created = len(plan_tasks)
        if tasks_created > 0:
            plan_generated = True
            mission.statut = models.MissionStatus.plan_pret

            try:
                plan_documents = generate_documents(mission.objectif, clean_reply, plan_tasks)
            except OrionAPIError as exc:
                db.commit()
                raise HTTPException(status_code=503, detail=str(exc)) from exc

            for doc_data in plan_documents:
                db.add(
                    models.Document(
                        mission_id=mission_id,
                        titre=doc_data["titre"],
                        type=doc_data["type"],
                        contenu=doc_data["contenu"],
                    )
                )
            documents_created = len(plan_documents)
            if documents_created > 0:
                documents_generated = True
                mission.statut = models.MissionStatus.documents_prets

                try:
                    action_data = propose_action(mission.objectif, clean_reply, plan_documents)
                except OrionAPIError as exc:
                    db.commit()
                    raise HTTPException(status_code=503, detail=str(exc)) from exc

                if action_data:
                    db.add(
                        models.Action(
                            mission_id=mission_id,
                            type=action_data["type"],
                            destinataire=action_data["destinataire"],
                            sujet=action_data["sujet"],
                            contenu=action_data["contenu"],
                        )
                    )
                    action_proposed = True
                    mission.statut = models.MissionStatus.action_en_attente

    db.commit()
    db.refresh(assistant_message)

    return schemas.ChatResponse(
        id=assistant_message.id,
        mission_id=assistant_message.mission_id,
        role=assistant_message.role,
        contenu=assistant_message.contenu,
        date_creation=assistant_message.date_creation,
        discovery_complete=discovery_complete,
        plan_generated=plan_generated,
        tasks_created=tasks_created,
        documents_generated=documents_generated,
        documents_created=documents_created,
        action_proposed=action_proposed,
    )


@router.post("/{mission_id}/retry", response_model=schemas.RetryResponse)
def retry_mission(mission_id: int, db: Session = Depends(get_db)):
    mission = db.query(models.Mission).filter(models.Mission.id == mission_id).first()
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable")

    if mission.statut == models.MissionStatus.nouvelle:
        raise HTTPException(
            status_code=400,
            detail="La phase de découverte n'est pas encore terminée pour cette mission.",
        )

    last_assistant = (
        db.query(models.Message)
        .filter(models.Message.mission_id == mission_id, models.Message.role == models.MessageRole.assistant)
        .order_by(models.Message.date_creation.desc())
        .first()
    )
    if last_assistant is None:
        raise HTTPException(
            status_code=400, detail="Aucune synthèse de découverte disponible pour cette mission."
        )
    conversation_summary = last_assistant.contenu

    existing_tasks = (
        db.query(models.Task)
        .filter(models.Task.mission_id == mission_id)
        .order_by(models.Task.ordre)
        .all()
    )
    plan_tasks = [{"titre": t.titre, "description": t.description or ""} for t in existing_tasks]

    existing_documents = (
        db.query(models.Document)
        .filter(models.Document.mission_id == mission_id)
        .order_by(models.Document.id)
        .all()
    )
    plan_documents = [{"titre": d.titre, "type": d.type, "contenu": d.contenu} for d in existing_documents]

    existing_actions_count = (
        db.query(models.Action).filter(models.Action.mission_id == mission_id).count()
    )

    plan_generated = len(plan_tasks) > 0
    tasks_created = 0
    documents_generated = len(plan_documents) > 0
    documents_created = 0
    action_proposed = existing_actions_count > 0

    if plan_generated and documents_generated and action_proposed:
        raise HTTPException(
            status_code=400, detail="Toutes les étapes ont déjà été générées pour cette mission."
        )

    if not plan_generated:
        try:
            new_tasks = generate_plan(mission.objectif, conversation_summary)
        except OrionAPIError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        for ordre, task_data in enumerate(new_tasks):
            db.add(
                models.Task(
                    mission_id=mission_id,
                    titre=task_data["titre"],
                    description=task_data.get("description"),
                    ordre=ordre,
                )
            )
        tasks_created = len(new_tasks)
        plan_tasks = new_tasks
        if tasks_created > 0:
            plan_generated = True
            mission.statut = models.MissionStatus.plan_pret
        db.commit()

    if plan_generated and not documents_generated:
        try:
            new_documents = generate_documents(mission.objectif, conversation_summary, plan_tasks)
        except OrionAPIError as exc:
            db.commit()
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        for doc_data in new_documents:
            db.add(
                models.Document(
                    mission_id=mission_id,
                    titre=doc_data["titre"],
                    type=doc_data["type"],
                    contenu=doc_data["contenu"],
                )
            )
        documents_created = len(new_documents)
        plan_documents = new_documents
        if documents_created > 0:
            documents_generated = True
            mission.statut = models.MissionStatus.documents_prets
        db.commit()

    if documents_generated and not action_proposed:
        try:
            action_data = propose_action(mission.objectif, conversation_summary, plan_documents)
        except OrionAPIError as exc:
            db.commit()
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        if action_data:
            db.add(
                models.Action(
                    mission_id=mission_id,
                    type=action_data["type"],
                    destinataire=action_data["destinataire"],
                    sujet=action_data["sujet"],
                    contenu=action_data["contenu"],
                )
            )
            action_proposed = True
            mission.statut = models.MissionStatus.action_en_attente
        db.commit()

    db.commit()

    return schemas.RetryResponse(
        plan_generated=plan_generated,
        tasks_created=tasks_created,
        documents_generated=documents_generated,
        documents_created=documents_created,
        action_proposed=action_proposed,
    )


@router.get("/{mission_id}/tasks", response_model=list[schemas.TaskOut])
def list_tasks(mission_id: int, db: Session = Depends(get_db)):
    mission = db.query(models.Mission).filter(models.Mission.id == mission_id).first()
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable")
    return (
        db.query(models.Task)
        .filter(models.Task.mission_id == mission_id)
        .order_by(models.Task.ordre)
        .all()
    )


@router.get("/{mission_id}/documents", response_model=list[schemas.DocumentOut])
def list_documents(mission_id: int, db: Session = Depends(get_db)):
    mission = db.query(models.Mission).filter(models.Mission.id == mission_id).first()
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable")
    return (
        db.query(models.Document)
        .filter(models.Document.mission_id == mission_id)
        .order_by(models.Document.id)
        .all()
    )


@router.get("/{mission_id}/documents/{doc_id}", response_model=schemas.DocumentOut)
def get_document(mission_id: int, doc_id: int, db: Session = Depends(get_db)):
    document = (
        db.query(models.Document)
        .filter(models.Document.mission_id == mission_id, models.Document.id == doc_id)
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document introuvable")
    return document


@router.get("/{mission_id}/actions", response_model=list[schemas.ActionOut])
def list_actions(mission_id: int, db: Session = Depends(get_db)):
    mission = db.query(models.Mission).filter(models.Mission.id == mission_id).first()
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable")
    return (
        db.query(models.Action)
        .filter(models.Action.mission_id == mission_id)
        .order_by(models.Action.id)
        .all()
    )


@router.post("/{mission_id}/actions/{action_id}/approve", response_model=schemas.ActionApprovalResponse)
def approve_action(mission_id: int, action_id: int, db: Session = Depends(get_db)):
    mission = db.query(models.Mission).filter(models.Mission.id == mission_id).first()
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable")

    action = (
        db.query(models.Action)
        .filter(models.Action.id == action_id, models.Action.mission_id == mission_id)
        .first()
    )
    if action is None:
        raise HTTPException(status_code=404, detail="Action introuvable")
    if action.statut != models.ActionStatus.en_attente:
        raise HTTPException(status_code=400, detail="Cette action a déjà été traitée.")

    simulated = not is_email_configured()
    try:
        send_email(action.destinataire, action.sujet, action.contenu)
    except EmailSendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    action.statut = models.ActionStatus.executee
    mission.statut = models.MissionStatus.terminee
    db.commit()
    db.refresh(action)

    message = (
        "Mode simulation : aucun email réel n'a été envoyé (configure SMTP_* dans le .env pour un envoi réel)."
        if simulated
        else "Email envoyé avec succès."
    )
    return schemas.ActionApprovalResponse(action=action, simulated=simulated, message=message)


@router.post("/{mission_id}/actions/{action_id}/reject", response_model=schemas.ActionOut)
def reject_action(mission_id: int, action_id: int, db: Session = Depends(get_db)):
    mission = db.query(models.Mission).filter(models.Mission.id == mission_id).first()
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable")

    action = (
        db.query(models.Action)
        .filter(models.Action.id == action_id, models.Action.mission_id == mission_id)
        .first()
    )
    if action is None:
        raise HTTPException(status_code=404, detail="Action introuvable")
    if action.statut != models.ActionStatus.en_attente:
        raise HTTPException(status_code=400, detail="Cette action a déjà été traitée.")

    action.statut = models.ActionStatus.rejetee
    db.commit()
    db.refresh(action)
    return action

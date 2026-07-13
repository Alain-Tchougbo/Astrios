import io
import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pypdf import PdfReader
from sqlalchemy.orm import Session

import google_calendar
import models
import schemas
from database import get_db
from gmail_sender import GmailSendError, send_gmail_message
from orion import (
    OrionAPIError,
    ask_orion,
    generate_documents,
    generate_plan,
    propose_action,
)

router = APIRouter(prefix="/missions", tags=["missions"])

MAX_ATTACHMENT_SIZE = 2 * 1024 * 1024  # 2 Mo
ALLOWED_ATTACHMENT_EXTENSIONS = {".txt", ".pdf"}


def _extract_attachment_text(filename: str, content: bytes) -> str:
    ext = os.path.splitext(filename)[1].lower()

    if ext == ".txt":
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return content.decode("latin-1", errors="replace")

    if ext == ".pdf":
        try:
            reader = PdfReader(io.BytesIO(content))
            pages_text = [page.extract_text() or "" for page in reader.pages]
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Impossible de lire ce PDF : {exc}") from exc
        return "\n".join(pages_text).strip() or "(aucun texte détecté dans le PDF)"

    raise HTTPException(
        status_code=400, detail="Format de fichier non supporté (.txt ou .pdf uniquement)."
    )


def _maybe_complete_mission(db: Session, mission: models.Mission, mission_id: int) -> None:
    # La mission ne passe à "terminee" que lorsque plus aucune action n'est en attente —
    # une mission peut désormais avoir plusieurs actions (ex: email + calendar_event)
    # proposées indépendamment par propose_action, toutes doivent être traitées.
    # db.flush() est nécessaire ici : la session est configurée en autoflush=False
    # (voir database.py), donc sans flush explicite, le changement de statut de
    # l'action qu'on vient de traiter (encore en attente d'écriture) ne serait pas
    # visible par la requête de comptage ci-dessous.
    db.flush()
    remaining = (
        db.query(models.Action)
        .filter(
            models.Action.mission_id == mission_id,
            models.Action.statut == models.ActionStatus.en_attente,
        )
        .count()
    )
    if remaining == 0:
        mission.statut = models.MissionStatus.terminee


def _build_action(mission_id: int, action_data: dict) -> models.Action:
    if action_data["type"] == "calendar_event":
        return models.Action(
            mission_id=mission_id,
            task_id=action_data.get("task_id"),
            type="calendar_event",
            details={
                "titre": action_data["titre"],
                "description": action_data.get("description", ""),
                "date_debut": action_data["date_debut"],
                "date_fin": action_data["date_fin"],
            },
        )
    return models.Action(
        mission_id=mission_id,
        task_id=action_data.get("task_id"),
        type="email",
        destinataire=action_data["destinataire"],
        sujet=action_data["sujet"],
        contenu=action_data["contenu"],
    )


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


@router.patch("/{mission_id}", response_model=schemas.MissionOut)
def update_mission(mission_id: int, payload: schemas.MissionUpdate, db: Session = Depends(get_db)):
    mission = db.query(models.Mission).filter(models.Mission.id == mission_id).first()
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable")

    titre = payload.titre.strip()
    if not titre:
        raise HTTPException(status_code=400, detail="Le titre ne peut pas être vide.")

    mission.titre = titre
    db.commit()
    db.refresh(mission)
    return mission


@router.delete("/{mission_id}", status_code=204)
def delete_mission(mission_id: int, db: Session = Depends(get_db)):
    mission = db.query(models.Mission).filter(models.Mission.id == mission_id).first()
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable")

    db.delete(mission)
    db.commit()


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
def chat_with_orion(
    mission_id: int,
    contenu: str = Form(...),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    mission = db.query(models.Mission).filter(models.Mission.id == mission_id).first()
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable")

    file_text = None
    attachment_note = None

    if file is not None and file.filename:
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ALLOWED_ATTACHMENT_EXTENSIONS:
            raise HTTPException(
                status_code=400, detail="Format de fichier non supporté (.txt ou .pdf uniquement)."
            )

        content = file.file.read()
        if len(content) > MAX_ATTACHMENT_SIZE:
            raise HTTPException(
                status_code=400, detail="Le fichier dépasse la taille maximale autorisée (2 Mo)."
            )

        file_text = _extract_attachment_text(file.filename, content)
        attachment_note = f"📎 Fichier joint : {file.filename}"

    # Le contenu persisté (et affiché dans le chat) reste léger : le texte tapé par
    # l'utilisateur + une simple mention du fichier joint. Le texte complet extrait du
    # fichier n'est injecté que dans l'appel Qwen de ce tour précis (voir plus bas),
    # pas dans l'historique persisté — pour éviter qu'un gros fichier ne gonfle
    # indéfiniment le contexte envoyé à Qwen à chaque tour suivant de la conversation.
    persisted_content = contenu
    if attachment_note:
        persisted_content = f"{contenu}\n\n{attachment_note}" if contenu.strip() else attachment_note

    user_message = models.Message(
        mission_id=mission_id, role=models.MessageRole.user, contenu=persisted_content
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
    if file_text:
        qwen_history[-1]["content"] = f"Contexte du fichier joint : {file_text}\n\nMessage : {contenu}"

    try:
        orion_reply = ask_orion(qwen_history)
    except OrionAPIError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    discovery_complete = orion_reply["discovery_complete"]
    clean_reply = orion_reply["message"]
    # Pas de suggestions sur un message de synthèse (discovery_complete) : la
    # conversation quitte la phase de questions, il n'y a plus rien à cliquer.
    suggestions = [] if discovery_complete else orion_reply["suggestions"]

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
    actions_created = 0

    if discovery_complete and mission.statut == models.MissionStatus.nouvelle:
        mission.statut = models.MissionStatus.en_cours

        try:
            plan_tasks = generate_plan(mission.objectif, clean_reply)
        except OrionAPIError as exc:
            db.commit()
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        task_objects = []
        for ordre, task_data in enumerate(plan_tasks):
            task_obj = models.Task(
                mission_id=mission_id,
                titre=task_data["titre"],
                description=task_data.get("description"),
                ordre=ordre,
            )
            db.add(task_obj)
            task_objects.append(task_obj)
        tasks_created = len(plan_tasks)
        if tasks_created > 0:
            db.flush()  # assigne les ids réels aux tasks avant de les passer à propose_action
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
                    actions_data = propose_action(
                        mission.objectif,
                        clean_reply,
                        [{"id": t.id, "titre": t.titre} for t in task_objects],
                        plan_documents,
                    )
                except OrionAPIError as exc:
                    db.commit()
                    raise HTTPException(status_code=503, detail=str(exc)) from exc

                for action_data in actions_data:
                    db.add(_build_action(mission_id, action_data))
                actions_created = len(actions_data)
                if actions_created > 0:
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
        actions_created=actions_created,
        suggestions=suggestions,
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
    task_objects = existing_tasks  # objets ORM avec id réel, utilisés pour propose_action

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
    actions_created = 0

    if plan_generated and documents_generated and action_proposed:
        raise HTTPException(
            status_code=400, detail="Toutes les étapes ont déjà été générées pour cette mission."
        )

    if not plan_generated:
        try:
            new_tasks = generate_plan(mission.objectif, conversation_summary)
        except OrionAPIError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        new_task_objects = []
        for ordre, task_data in enumerate(new_tasks):
            task_obj = models.Task(
                mission_id=mission_id,
                titre=task_data["titre"],
                description=task_data.get("description"),
                ordre=ordre,
            )
            db.add(task_obj)
            new_task_objects.append(task_obj)
        tasks_created = len(new_tasks)
        plan_tasks = new_tasks
        if tasks_created > 0:
            db.flush()  # assigne les ids réels aux tasks avant de les passer à propose_action
            plan_generated = True
            mission.statut = models.MissionStatus.plan_pret
            task_objects = new_task_objects
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
            actions_data = propose_action(
                mission.objectif,
                conversation_summary,
                [{"id": t.id, "titre": t.titre} for t in task_objects],
                plan_documents,
            )
        except OrionAPIError as exc:
            db.commit()
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        for action_data in actions_data:
            db.add(_build_action(mission_id, action_data))
        actions_created = len(actions_data)
        if actions_created > 0:
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
        actions_created=actions_created,
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


@router.patch("/{mission_id}/documents/{doc_id}", response_model=schemas.DocumentOut)
def update_document(
    mission_id: int, doc_id: int, payload: schemas.DocumentUpdate, db: Session = Depends(get_db)
):
    document = (
        db.query(models.Document)
        .filter(models.Document.mission_id == mission_id, models.Document.id == doc_id)
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document introuvable")

    contenu = payload.contenu.strip()
    if not contenu:
        raise HTTPException(status_code=400, detail="Le contenu du document ne peut pas être vide.")

    document.contenu = contenu
    db.commit()
    db.refresh(document)
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


@router.patch("/{mission_id}/actions/{action_id}", response_model=schemas.ActionOut)
def update_action(
    mission_id: int, action_id: int, payload: schemas.ActionEmailUpdate, db: Session = Depends(get_db)
):
    action = (
        db.query(models.Action)
        .filter(models.Action.id == action_id, models.Action.mission_id == mission_id)
        .first()
    )
    if action is None:
        raise HTTPException(status_code=404, detail="Action introuvable")
    if action.type != "email":
        raise HTTPException(status_code=400, detail="Seul le contenu d'une action email peut être modifié.")
    if action.statut != models.ActionStatus.en_attente:
        raise HTTPException(status_code=400, detail="Cette action a déjà été traitée.")

    destinataire = payload.destinataire.strip()
    sujet = payload.sujet.strip()
    contenu = payload.contenu.strip()
    if not (destinataire and sujet and contenu):
        raise HTTPException(status_code=400, detail="Destinataire, sujet et contenu sont obligatoires.")

    action.destinataire = destinataire
    action.sujet = sujet
    action.contenu = contenu
    db.commit()
    db.refresh(action)
    return action


def _mark_linked_task_done(db: Session, mission_id: int, action: models.Action) -> None:
    # Auto-cochage fiable : l'action porte l'id exact de la tâche qu'elle accomplit,
    # déterminé par Qwen au moment de la proposition (voir orion.propose_action).
    # Une mission peut avoir plusieurs actions ciblant des tâches différentes (ou la
    # même) : chaque exécution coche la tâche liée à CETTE action précise — les
    # tâches sans action correspondante restent "a_faire" faute d'action associée.
    # Aucun cochage manuel n'est prévu côté UI (les checkboxes sont en lecture seule).
    if action.task_id is None:
        return
    linked_task = (
        db.query(models.Task)
        .filter(models.Task.id == action.task_id, models.Task.mission_id == mission_id)
        .first()
    )
    if linked_task is not None:
        linked_task.statut = models.TaskStatus.terminee


def _execute_action(db: Session, mission_id: int, action: models.Action) -> str:
    """Exécute réellement l'action (envoi Gmail ou création Calendar), la marque comme
    exécutée et coche la tâche liée. Laisse remonter CalendarError/GmailSendError en cas
    d'échec côté API externe — l'action reste alors "en_attente", l'appelant décide
    comment traiter l'échec (erreur HTTP directe pour une approbation unique, résultat
    partiel pour un lot)."""
    if action.type == "calendar_event":
        details = action.details or {}
        google_calendar.create_calendar_event(
            details.get("titre", ""),
            details.get("description", ""),
            details.get("date_debut"),
            details.get("date_fin"),
        )
        message = "Événement créé dans Google Calendar."
    else:
        send_gmail_message(action.destinataire, action.sujet, action.contenu)
        message = "Email envoyé avec succès."

    action.statut = models.ActionStatus.executee
    _mark_linked_task_done(db, mission_id, action)
    return message


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

    if not google_calendar.is_connected():
        raise HTTPException(
            status_code=409,
            detail="Aucun compte Google connecté. Connecte-toi via /auth/google/login avant d'approuver cette action.",
        )

    try:
        message = _execute_action(db, mission_id, action)
    except (google_calendar.CalendarError, GmailSendError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    _maybe_complete_mission(db, mission, mission_id)
    db.commit()
    db.refresh(action)

    return schemas.ActionApprovalResponse(action=action, message=message)


@router.post("/{mission_id}/actions/approve-all", response_model=schemas.ApproveAllResponse)
def approve_all_actions(mission_id: int, db: Session = Depends(get_db)):
    mission = db.query(models.Mission).filter(models.Mission.id == mission_id).first()
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable")

    pending_actions = (
        db.query(models.Action)
        .filter(models.Action.mission_id == mission_id, models.Action.statut == models.ActionStatus.en_attente)
        .order_by(models.Action.id)
        .all()
    )
    if not pending_actions:
        raise HTTPException(status_code=400, detail="Aucune action en attente pour cette mission.")

    if not google_calendar.is_connected():
        raise HTTPException(
            status_code=409,
            detail="Aucun compte Google connecté. Connecte-toi via /auth/google/login avant d'approuver cette action.",
        )

    # outcomes garde les objets ORM bruts (pas encore convertis en schéma Pydantic) pour
    # pouvoir les rafraîchir après le commit ci-dessous, avant de construire la réponse.
    outcomes = []
    for action in pending_actions:
        try:
            message = _execute_action(db, mission_id, action)
            outcomes.append((action, True, message))
        except (google_calendar.CalendarError, GmailSendError) as exc:
            # L'action reste "en_attente" : elle pourra être retentée lors d'un prochain
            # appel à approve-all, sans perdre les autres actions déjà exécutées.
            outcomes.append((action, False, str(exc)))

    _maybe_complete_mission(db, mission, mission_id)
    db.commit()
    db.refresh(mission)
    for action, _success, _message in outcomes:
        db.refresh(action)

    results = [
        schemas.ActionExecutionResult(action=action, success=success, message=message)
        for action, success, message in outcomes
    ]
    return schemas.ApproveAllResponse(results=results, mission_statut=mission.statut)


@router.post("/{mission_id}/actions/{action_id}/reject", response_model=schemas.ActionOut)
@router.post("/{mission_id}/actions/{action_id}/exclude", response_model=schemas.ActionOut)
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
    _maybe_complete_mission(db, mission, mission_id)
    db.commit()
    db.refresh(action)
    return action

import difflib
import io
import logging
import os
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pypdf import PdfReader
from sqlalchemy.orm import Session

import google_calendar
import ics_builder
import models
import rate_limiter
import schemas
import smtp_sender
from database import get_db
from gmail_sender import GmailSendError, send_gmail_message
from orion import (
    OrionAPIError,
    ask_orion,
    extract_mission_facts,
    generate_documents,
    generate_plan,
    propose_action,
)

logger = logging.getLogger("missions")

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
        # Une tâche du plan n'est cochée automatiquement que si une action précise
        # lui est liée (voir _mark_linked_task_done) — or le plan compte souvent
        # plus de tâches (4 à 8) que d'actions générées (typiquement 1 ou 2 :
        # email + événement), donc des tâches de préparation (déjà couvertes par
        # les documents générés) restaient "à faire" indéfiniment alors que la
        # mission est bel et bien terminée. Une fois qu'il n'y a plus aucune action
        # en attente, la mission est considérée achevée : le reste du plan doit
        # visuellement refléter cet état plutôt que rester bloqué à mi-chemin.
        db.query(models.Task).filter(
            models.Task.mission_id == mission_id,
            models.Task.statut == models.TaskStatus.a_faire,
        ).update({models.Task.statut: models.TaskStatus.terminee})


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


def _get_conversation_summary(db: Session, mission_id: int) -> str:
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
    return last_assistant.contenu


def _run_generate_plan(db: Session, mission: models.Mission, conversation_summary: str) -> int:
    """Génère le plan et le persiste — appelée depuis /chat, /generate-plan et /retry.
    Si des tâches existent déjà pour cette mission (un nouveau besoin est apparu APRÈS une
    première génération), generate_plan bascule en mode additif et ne renvoie que les
    tâches manquantes pour ce nouveau besoin, jamais un plan refait ni dupliqué."""
    existing_tasks = (
        db.query(models.Task)
        .filter(models.Task.mission_id == mission.id)
        .order_by(models.Task.ordre)
        .all()
    )
    existing_tasks_data = [{"titre": t.titre, "description": t.description or ""} for t in existing_tasks]
    next_ordre = (existing_tasks[-1].ordre + 1) if existing_tasks else 0

    try:
        plan_tasks = generate_plan(
            mission.objectif, conversation_summary, mission.mission_facts, existing_tasks_data
        )
    except OrionAPIError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    for offset, task_data in enumerate(plan_tasks):
        db.add(
            models.Task(
                mission_id=mission.id,
                titre=task_data["titre"],
                description=task_data.get("description"),
                ordre=next_ordre + offset,
            )
        )
    tasks_created = len(plan_tasks)
    # Ne fait avancer le statut que depuis un état antérieur au plan — sur un ajout
    # incrémental à une mission déjà plus avancée (documents_prets/action_en_attente/
    # terminee), on ne régresse jamais le statut affiché : /generate-documents et
    # /generate-actions le feront de nouveau progresser juste après dans le même flux.
    if tasks_created > 0 and mission.statut in (
        models.MissionStatus.nouvelle,
        models.MissionStatus.en_cours,
    ):
        mission.statut = models.MissionStatus.plan_pret
    db.commit()
    return tasks_created


def _run_generate_documents(db: Session, mission: models.Mission, conversation_summary: str) -> int:
    """Génère les documents à partir du plan déjà persisté. Si des documents existent déjà
    (nouveau besoin apparu après une première génération), generate_documents bascule en
    mode additif et ne renvoie que les documents manquants pour ce nouveau besoin."""
    existing_tasks = (
        db.query(models.Task)
        .filter(models.Task.mission_id == mission.id)
        .order_by(models.Task.ordre)
        .all()
    )
    plan_tasks = [{"titre": t.titre, "description": t.description or ""} for t in existing_tasks]

    existing_documents = (
        db.query(models.Document)
        .filter(models.Document.mission_id == mission.id)
        .order_by(models.Document.id)
        .all()
    )
    existing_documents_data = [
        {"titre": d.titre, "type": d.type, "is_email_action_source": bool(d.is_email_source)}
        for d in existing_documents
    ]

    try:
        plan_documents = generate_documents(
            mission.objectif, conversation_summary, plan_tasks, mission.mission_facts, existing_documents_data
        )
    except OrionAPIError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    for doc_data in plan_documents:
        db.add(
            models.Document(
                mission_id=mission.id,
                titre=doc_data["titre"],
                type=doc_data["type"],
                purpose=doc_data.get("purpose") or None,
                is_email_source=doc_data.get("is_email_action_source", False),
                contenu=doc_data["contenu"],
            )
        )
    documents_created = len(plan_documents)
    if documents_created > 0 and mission.statut in (
        models.MissionStatus.nouvelle,
        models.MissionStatus.en_cours,
        models.MissionStatus.plan_pret,
    ):
        mission.statut = models.MissionStatus.documents_prets
    db.commit()
    return documents_created


def _find_email_source_document(db: Session, mission_id: int) -> models.Document | None:
    """Plusieurs documents "source d'email" peuvent exister au fil des ajouts incrémentaux
    (un email d'invitation au round 1, un email de suivi à un autre destinataire au round
    2, etc.) — chacun ne doit être réutilisé QU'UNE FOIS, par l'action email du round qui
    l'a généré. On renvoie donc le plus ancien qui n'est pas déjà repris mot pour mot par
    une action email existante, plutôt que systématiquement le tout premier de la mission."""
    email_source_docs = (
        db.query(models.Document)
        .filter(models.Document.mission_id == mission_id, models.Document.is_email_source.is_(True))
        .order_by(models.Document.id)
        .all()
    )
    if not email_source_docs:
        return None
    already_used_contents = {
        a.contenu
        for a in db.query(models.Action)
        .filter(models.Action.mission_id == mission_id, models.Action.type == "email")
        .all()
    }
    for doc in email_source_docs:
        if doc.contenu not in already_used_contents:
            return doc
    return None


def _compute_event_datetimes_from_facts(mission_facts: dict | None) -> tuple[str, str] | None:
    """Si mission_facts contient un rendez-vous entièrement déterminé (date + heure +
    durée), calcule date_debut/date_fin à partir de CES valeurs exactes — pour que
    l'action calendrier ne puisse jamais diverger de ce que l'utilisateur a réellement
    dit (bug observé : un document annonçant "30 minutes" pendant qu'un événement de
    60 minutes était créé indépendamment par propose_action)."""
    if not mission_facts:
        return None
    for rdv in mission_facts.get("rendez_vous") or []:
        date, heure, duree = rdv.get("date"), rdv.get("heure"), rdv.get("duree_minutes")
        if not (date and heure and duree):
            continue
        try:
            debut = datetime.strptime(f"{date}T{heure}", "%Y-%m-%dT%H:%M")
        except (ValueError, TypeError):
            continue
        fin = debut + timedelta(minutes=int(duree))
        return debut.strftime("%Y-%m-%dT%H:%M:%S"), fin.strftime("%Y-%m-%dT%H:%M:%S")
    return None


def _run_generate_actions(db: Session, mission: models.Mission, conversation_summary: str) -> tuple[int, bool]:
    """Propose les actions à partir du plan et des documents déjà persistés. Si des
    actions existent déjà (nouveau besoin apparu après une première génération, ou même
    après exécution complète), propose_action bascule en mode additif et ne renvoie que
    les actions manquantes pour ce nouveau besoin — la mission est alors "rouverte"
    (statut action_en_attente) même si elle était déjà terminee."""
    existing_tasks = (
        db.query(models.Task)
        .filter(models.Task.mission_id == mission.id)
        .order_by(models.Task.ordre)
        .all()
    )
    existing_documents = (
        db.query(models.Document)
        .filter(models.Document.mission_id == mission.id)
        .order_by(models.Document.id)
        .all()
    )
    plan_documents = [
        {"titre": d.titre, "type": d.type, "contenu": d.contenu, "purpose": d.purpose or ""}
        for d in existing_documents
    ]

    existing_actions = (
        db.query(models.Action)
        .filter(models.Action.mission_id == mission.id)
        .order_by(models.Action.id)
        .all()
    )
    existing_actions_data = [
        {"type": a.type, "destinataire": a.destinataire, "titre": (a.details or {}).get("titre")}
        for a in existing_actions
    ]

    try:
        actions_data = propose_action(
            mission.objectif,
            conversation_summary,
            [{"id": t.id, "titre": t.titre} for t in existing_tasks],
            plan_documents,
            mission.mission_facts,
            existing_actions_data,
        )
    except OrionAPIError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # Garde-fou déterministe côté code — pas seulement côté prompt : une action email
    # sans adresse réelle dans mission_facts ne doit JAMAIS être créée (règle produit :
    # l'anticipation d'un besoin d'email se fait par UNE question en découverte, jamais
    # par une action incomplète avec une adresse de test/inventée). On ne fait plus
    # confiance à Qwen seul pour respecter cette règle : toute action email dont le
    # destinataire ne correspond à aucune adresse connue de mission_facts est rejetée
    # silencieusement (juste loggée), jamais persistée.
    valid_emails = {
        (d.get("email") or "").strip().lower()
        for d in (mission.mission_facts or {}).get("destinataires") or []
        if (d.get("email") or "").strip()
    }
    filtered_actions_data = []
    for action_data in actions_data:
        if action_data["type"] == "email":
            destinataire = (action_data.get("destinataire") or "").strip().lower()
            if destinataire not in valid_emails:
                logger.warning(
                    "Action email rejetée pour la mission %s : destinataire %r absent de "
                    "mission_facts (aucune adresse réelle collectée en découverte).",
                    mission.id,
                    action_data.get("destinataire"),
                )
                continue
        filtered_actions_data.append(action_data)
    actions_data = filtered_actions_data

    # Cohérence forcée entre livrables : un document marqué comme source de l'email
    # (voir generate_documents) prime toujours sur une régénération indépendante de
    # l'action email ; un rendez-vous entièrement déterminé dans mission_facts prime
    # toujours sur la date/durée que propose_action aurait déduites de son côté.
    email_source_doc = _find_email_source_document(db, mission.id)
    event_datetimes = _compute_event_datetimes_from_facts(mission.mission_facts)
    for action_data in actions_data:
        if action_data["type"] == "email" and email_source_doc is not None:
            action_data["sujet"] = email_source_doc.titre
            action_data["contenu"] = email_source_doc.contenu
        elif action_data["type"] == "calendar_event" and event_datetimes is not None:
            action_data["date_debut"], action_data["date_fin"] = event_datetimes

    # Garde-fou déterministe : malgré la consigne de ne pas dupliquer (voir
    # scope_instruction dans propose_action), Qwen reproposait parfois EXACTEMENT la
    # même action qu'un round précédent (ex. le même événement calendrier déjà
    # existant) en même temps qu'une action réellement nouvelle. On ne peut pas se
    # reposer uniquement sur le prompt pour ça — même pattern que has_email_intent/le
    # garde-fou placeholder : un contrôle déterministe au-dessus du jugement du LLM.
    #
    # La clé de comparaison inclut le sujet/titre, pas seulement le destinataire/les
    # dates : un email au MÊME destinataire mais avec un sujet différent (ex. un
    # rappel après une première invitation déjà exécutée) est une action NOUVELLE,
    # pas un doublon — dédupliquer sur le seul destinataire la faisait disparaître
    # silencieusement (bug observé : une mission rouverte pour ajouter un rappel au
    # même contact ne créait plus aucune action).
    existing_emails = {
        ((a.destinataire or "").strip().lower(), (a.sujet or "").strip().lower())
        for a in existing_actions
        if a.type == "email"
    }
    existing_events = {
        (
            (a.details or {}).get("date_debut"),
            (a.details or {}).get("date_fin"),
            ((a.details or {}).get("titre") or "").strip().lower(),
        )
        for a in existing_actions
        if a.type == "calendar_event"
    }
    deduped_actions_data = []
    for action_data in actions_data:
        if action_data["type"] == "email" and (
            (action_data.get("destinataire") or "").strip().lower(),
            (action_data.get("sujet") or "").strip().lower(),
        ) in existing_emails:
            continue
        if action_data["type"] == "calendar_event" and (
            action_data.get("date_debut"),
            action_data.get("date_fin"),
            (action_data.get("titre") or "").strip().lower(),
        ) in existing_events:
            continue
        deduped_actions_data.append(action_data)
    actions_data = deduped_actions_data

    for action_data in actions_data:
        db.add(_build_action(mission.id, action_data))
    actions_created = len(actions_data)
    action_proposed = actions_created > 0
    if action_proposed:
        # Volontairement inconditionnel : une nouvelle action rouvre la mission même si
        # elle était déjà "terminee" (toutes les actions précédentes exécutées) — c'est
        # le comportement attendu pour un ajout incrémental après exécution complète.
        mission.statut = models.MissionStatus.action_en_attente
    db.commit()
    return actions_created, action_proposed


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

    # Réouverture : un nouveau message sur une mission déjà "terminee" relance
    # immédiatement le cycle de vie, AVANT même de savoir si ce message va réellement
    # déclencher une nouvelle génération. Sans ça, le badge/la timeline restaient
    # figés sur "Terminée" pendant toute la nouvelle conversation de découverte,
    # jusqu'à ce qu'une nouvelle action soit enfin proposée à la toute fin du pipeline
    # (bug rapporté : le backend traite le message et génère un nouveau plan, mais
    # l'état affiché de la mission ne bouge pas pendant tout ce temps).
    if mission.statut == models.MissionStatus.terminee:
        mission.statut = models.MissionStatus.en_cours

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

    # La génération du plan/documents/actions n'a plus lieu ici : une fois la découverte
    # terminée, le frontend appelle explicitement /generate-plan, /generate-documents
    # puis /generate-actions, un par un — ce qui permet d'afficher une vraie progression
    # séquentielle (message de début, appel réseau réel, message de fin) au lieu de tout
    # exécuter d'un coup dans ce seul appel.
    #
    # IMPORTANT : ce déclenchement n'est plus limité à mission.statut == nouvelle. Une
    # mission déjà entièrement générée (voire déjà exécutée) peut recevoir un NOUVEAU
    # besoin dans le chat (ex. "je dois aussi lui envoyer un mail") ; si la découverte
    # de CE besoin aboutit à son tour, on ré-extrait mission_facts (à partir de la
    # conversation COMPLÈTE, donc cumulatif avec les faits déjà connus) et on relance le
    # même pipeline /generate-plan -> /generate-documents -> /generate-actions — mais ces
    # trois étapes savent maintenant fonctionner en mode additif (voir _run_generate_plan
    # etc.) : elles n'ajoutent QUE ce qui manque encore, sans dupliquer l'existant. Voir
    # bug rapporté : "Le plan a déjà été généré pour cette mission" bloquait tout ajout.
    if discovery_complete:
        try:
            mission_facts = extract_mission_facts(mission.objectif, qwen_history)
        except OrionAPIError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        # Garde-fou déterministe supplémentaire : si un VRAI destinataire email est
        # maintenant connu (une adresse concrète dans mission_facts) mais qu'aucun nom
        # d'expéditeur n'a été donné pour signer, on refuse de conclure la découverte —
        # sinon les documents/actions email généreraient un placeholder de signature
        # ("[Votre prénom]") faute de mieux. Basé sur une adresse RÉELLE plutôt que sur
        # has_email_intent (mots-clés) : si l'utilisateur décline la question proactive
        # d'anticipation ("Souhaitez-vous que j'envoie une invitation ?"), le mot "email"
        # reste présent dans l'historique (posé par Orion lui-même) sans qu'aucune adresse
        # n'ait été acceptée — il ne faut alors PAS redemander un nom d'expéditeur pour un
        # email qui ne sera jamais envoyé.
        has_real_email_recipient = any(
            (d.get("email") or "").strip() for d in mission_facts.get("destinataires") or []
        )
        if has_real_email_recipient and not mission_facts.get("sender_name"):
            discovery_complete = False
            suggestions = []
            clean_reply = "Une dernière précision avant de conclure : au nom de qui dois-je signer cet email ?"
        else:
            mission.mission_facts = mission_facts
            if mission.statut == models.MissionStatus.nouvelle:
                mission.statut = models.MissionStatus.en_cours

    assistant_message = models.Message(
        mission_id=mission_id, role=models.MessageRole.assistant, contenu=clean_reply
    )
    db.add(assistant_message)

    db.commit()
    db.refresh(assistant_message)

    return schemas.ChatResponse(
        id=assistant_message.id,
        mission_id=assistant_message.mission_id,
        role=assistant_message.role,
        contenu=assistant_message.contenu,
        date_creation=assistant_message.date_creation,
        discovery_complete=discovery_complete,
        suggestions=suggestions,
    )


@router.post("/{mission_id}/generate-plan", response_model=schemas.GeneratePlanResponse)
def generate_plan_endpoint(mission_id: int, db: Session = Depends(get_db)):
    """Appelable plusieurs fois pour une même mission : si un plan existe déjà,
    _run_generate_plan bascule en mode additif (voir generate_plan) et n'ajoute que ce
    qu'un nouveau besoin exprimé après coup rend nécessaire — jamais de plan dupliqué."""
    mission = db.query(models.Mission).filter(models.Mission.id == mission_id).first()
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable")

    conversation_summary = _get_conversation_summary(db, mission_id)
    tasks_created = _run_generate_plan(db, mission, conversation_summary)
    return schemas.GeneratePlanResponse(tasks_created=tasks_created)


@router.post("/{mission_id}/generate-documents", response_model=schemas.GenerateDocumentsResponse)
def generate_documents_endpoint(mission_id: int, db: Session = Depends(get_db)):
    """Appelable plusieurs fois pour une même mission (voir generate_plan_endpoint) :
    seul le prérequis structurel (un plan doit exister) bloque encore, pas la présence
    de documents déjà générés."""
    mission = db.query(models.Mission).filter(models.Mission.id == mission_id).first()
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable")

    tasks_count = db.query(models.Task).filter(models.Task.mission_id == mission_id).count()
    if tasks_count == 0:
        raise HTTPException(status_code=400, detail="Le plan doit être généré avant les documents.")

    conversation_summary = _get_conversation_summary(db, mission_id)
    documents_created = _run_generate_documents(db, mission, conversation_summary)
    return schemas.GenerateDocumentsResponse(documents_created=documents_created)


@router.post("/{mission_id}/generate-actions", response_model=schemas.GenerateActionsResponse)
def generate_actions_endpoint(mission_id: int, db: Session = Depends(get_db)):
    """Appelable plusieurs fois pour une même mission (voir generate_plan_endpoint) : une
    nouvelle action peut être proposée même si la mission a déjà des actions — y compris
    déjà exécutées — tant qu'un document existe. La mission repasse alors en
    action_en_attente (voir _run_generate_actions), même si elle était terminee."""
    mission = db.query(models.Mission).filter(models.Mission.id == mission_id).first()
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable")

    documents_count = db.query(models.Document).filter(models.Document.mission_id == mission_id).count()
    if documents_count == 0:
        raise HTTPException(
            status_code=400, detail="Les documents doivent être générés avant de proposer une action."
        )

    conversation_summary = _get_conversation_summary(db, mission_id)
    actions_created, action_proposed = _run_generate_actions(db, mission, conversation_summary)

    # Referme la boucle d'une réouverture qui n'aboutit finalement à rien de nouveau :
    # si aucune action n'a été proposée ici ET qu'il n'y a plus aucune action en
    # attente par ailleurs, _maybe_complete_mission remet le statut à "terminee" au
    # lieu de laisser la mission bloquée sur "en_cours" indéfiniment. Si une nouvelle
    # action VIENT d'être proposée, il en reste au moins une en attente : cet appel ne
    # fait alors rien (la mission reste action_en_attente, posé juste au-dessus).
    _maybe_complete_mission(db, mission, mission_id)
    db.commit()
    return schemas.GenerateActionsResponse(actions_created=actions_created, action_proposed=action_proposed)


@router.post("/{mission_id}/retry", response_model=schemas.RetryResponse)
def retry_mission(mission_id: int, db: Session = Depends(get_db)):
    """Reprend la génération là où elle s'est arrêtée, en une seule fois — utile pour
    rattraper une mission bloquée par un échec Qwen ponctuel (bouton "Réessayer" sur un
    message d'erreur). Le flux normal passe par /generate-plan, /generate-documents et
    /generate-actions appelés séparément par le frontend pour une progression animée."""
    mission = db.query(models.Mission).filter(models.Mission.id == mission_id).first()
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission introuvable")

    if mission.statut == models.MissionStatus.nouvelle:
        raise HTTPException(
            status_code=400,
            detail="La phase de découverte n'est pas encore terminée pour cette mission.",
        )

    conversation_summary = _get_conversation_summary(db, mission_id)

    tasks_count = db.query(models.Task).filter(models.Task.mission_id == mission_id).count()
    documents_count = db.query(models.Document).filter(models.Document.mission_id == mission_id).count()
    existing_actions_count = (
        db.query(models.Action).filter(models.Action.mission_id == mission_id).count()
    )

    plan_generated = tasks_count > 0
    tasks_created = 0
    documents_generated = documents_count > 0
    documents_created = 0
    action_proposed = existing_actions_count > 0
    actions_created = 0

    if plan_generated and documents_generated and action_proposed:
        raise HTTPException(
            status_code=400, detail="Toutes les étapes ont déjà été générées pour cette mission."
        )

    if not plan_generated:
        tasks_created = _run_generate_plan(db, mission, conversation_summary)
        plan_generated = tasks_created > 0

    if plan_generated and not documents_generated:
        documents_created = _run_generate_documents(db, mission, conversation_summary)
        documents_generated = documents_created > 0

    if documents_generated and not action_proposed:
        actions_created, action_proposed = _run_generate_actions(db, mission, conversation_summary)

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


def _normalize_event_datetime(value: str) -> str:
    # Un <input type="datetime-local"> renvoie "YYYY-MM-DDTHH:MM" (sans secondes) —
    # on complète pour rester cohérent avec le format "YYYY-MM-DDTHH:MM:SS" utilisé
    # partout ailleurs (généré par Qwen, stocké en base, envoyé à Google Calendar).
    value = value.strip()
    if len(value) == 16:
        value += ":00"
    return value


@router.patch("/{mission_id}/actions/{action_id}", response_model=schemas.ActionOut)
def update_action(
    mission_id: int, action_id: int, payload: schemas.ActionUpdate, db: Session = Depends(get_db)
):
    action = (
        db.query(models.Action)
        .filter(models.Action.id == action_id, models.Action.mission_id == mission_id)
        .first()
    )
    if action is None:
        raise HTTPException(status_code=404, detail="Action introuvable")
    if action.statut != models.ActionStatus.en_attente:
        raise HTTPException(status_code=400, detail="Cette action a déjà été traitée.")

    if action.type == "calendar_event":
        titre = (payload.titre or "").strip()
        date_debut = _normalize_event_datetime(payload.date_debut or "")
        date_fin = _normalize_event_datetime(payload.date_fin or "")
        description = (payload.description or "").strip()
        participants = (payload.participants or "").strip()
        if not (titre and date_debut and date_fin):
            raise HTTPException(
                status_code=400, detail="Titre, date de début et date de fin sont obligatoires."
            )
        # Réassigner un nouveau dict (plutôt que muter action.details en place) est
        # nécessaire pour que SQLAlchemy détecte le changement sur cette colonne JSON.
        action.details = {
            "titre": titre,
            "description": description,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "participants": participants,
        }
    else:
        destinataire = (payload.destinataire or "").strip()
        sujet = (payload.sujet or "").strip()
        contenu = (payload.contenu or "").strip()
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


_SERVER_MODE_ERRORS = (
    google_calendar.CalendarError,
    GmailSendError,
    smtp_sender.SmtpSendError,
    smtp_sender.ServerModeValidationError,
    rate_limiter.RateLimitExceeded,
)


def _determine_execution_mode(force_simulation: bool = False) -> str:
    # Logique à trois niveaux : un compte Google utilisateur connecté (le cas
    # "normal") passe toujours devant ; sinon, si le serveur a un compte de démo
    # configuré, on l'utilise pour que n'importe quel visiteur (ex. jury du
    # hackathon) puisse tester une exécution réelle sans jamais toucher à OAuth ;
    # en dernier recours, simulation pure (aucun appel externe).
    if force_simulation:
        return "simulation"
    if google_calendar.is_connected():
        return "oauth"
    if smtp_sender.is_smtp_configured():
        return "server"
    return "simulation"


def _resolve_organizer(mode: str) -> tuple[str, str]:
    """Détermine qui doit apparaître comme ORGANIZER dans le .ics — et par cohérence,
    comme expéditeur réel du message : en mode "oauth", ce DOIT être le compte Google
    connecté (celui qui envoie réellement l'email via l'API Gmail), jamais le compte
    de démo SMTP. Sans ça, l'expéditeur du mail (compte utilisateur) et l'organisateur
    affiché dans le bandeau Gmail (Orion) sont incohérents, et les réponses RSVP
    partent vers le mauvais compte (bug rapporté). CN retombe sur l'adresse elle-même
    quand aucun nom d'affichage n'est disponible (le scope OAuth actuel ne donne pas
    accès au nom du profil, seulement à l'adresse via l'API Gmail).
    En mode "server", comportement historique inchangé : le compte de démo SMTP."""
    if mode == "oauth":
        email = google_calendar.get_connected_account_email()
        if email:
            return email, email
        # Filet de sécurité si le profil Gmail est injoignable (ne devrait normalement
        # pas arriver puisque mode == "oauth" implique un compte connecté valide) :
        # mieux vaut un organizer cohérent avec le compte de démo que planter l'envoi.
        logger.warning("Organizer oauth introuvable, repli sur le compte de démo SMTP.")
    return smtp_sender.SMTP_USER, smtp_sender.SENDER_DISPLAY_NAME


def _resolve_calendar_recipient(db: Session, mission_id: int, action: models.Action) -> str | None:
    """En mode serveur, un calendar_event n'a pas de champ "destinataire" dédié (à
    la différence d'un email) — on cherche une adresse dans le champ libre
    "participants" de l'action, sinon on retombe sur le destinataire de l'action
    email de la même mission (cas fréquent : email de confirmation + événement
    calendrier ciblent la même personne, ex. un entretien candidat)."""
    details = action.details or {}
    match = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", details.get("participants") or "")
    if match:
        return match.group(0)

    email_action = (
        db.query(models.Action)
        .filter(models.Action.mission_id == mission_id, models.Action.type == "email")
        .first()
    )
    if email_action and email_action.destinataire:
        return email_action.destinataire
    return None


def _execute_action(db: Session, mission_id: int, action: models.Action, mode: str) -> str:
    """Exécute réellement l'action selon le mode déterminé pour ce lot (voir
    _determine_execution_mode). Laisse remonter les erreurs des modes "oauth" et
    "server" (voir _SERVER_MODE_ERRORS) — l'action reste alors "en_attente", à
    l'appelant de décider comment traiter l'échec (HTTP direct pour une approbation
    unique, résultat partiel pour un lot). Marque l'action exécutée et coche la
    tâche liée uniquement en cas de succès."""
    if mode == "oauth":
        if action.type == "calendar_event":
            logger.info(
                "Envoi via Gmail API (compte user) : création d'événement Google Calendar (mission %s).",
                mission_id,
            )
            details = action.details or {}
            google_calendar.create_calendar_event(
                details.get("titre", ""),
                details.get("description", ""),
                details.get("date_debut"),
                details.get("date_fin"),
            )
            message = "Événement créé dans Google Calendar."
        else:
            logger.info(
                "Envoi via Gmail API (compte user) : email seul, sans événement associé (mission %s).",
                mission_id,
            )
            send_gmail_message(action.destinataire, action.sujet, action.contenu)
            message = "Email envoyé avec succès."

    elif mode == "server":
        if action.type == "calendar_event":
            details = action.details or {}
            recipient = _resolve_calendar_recipient(db, mission_id, action)
            if not recipient:
                raise smtp_sender.ServerModeValidationError(
                    "Aucun destinataire déterminable pour l'invitation calendrier — "
                    "ajoute une adresse email dans le champ Participants de l'action."
                )
            smtp_sender.validate_real_recipient(recipient)
            smtp_sender.validate_no_placeholder(details.get("description") or "")
            organizer_email, organizer_name = _resolve_organizer(mode)
            ics_content = ics_builder.build_ics_invite(
                titre=details.get("titre", ""),
                description=details.get("description", ""),
                date_debut=details.get("date_debut"),
                date_fin=details.get("date_fin"),
                organizer_email=organizer_email,
                organizer_name=organizer_name,
                attendee_email=recipient,
            )
            calendar_link = ics_builder.build_google_calendar_link(
                titre=details.get("titre", ""),
                description=details.get("description", ""),
                date_debut=details.get("date_debut"),
                date_fin=details.get("date_fin"),
            )
            logger.info(
                "Envoi via SMTP serveur : invitation calendrier (avec .ics + bouton) à %s (mission %s).",
                recipient,
                mission_id,
            )
            rate_limiter.check_and_record_email()
            smtp_sender.send_smtp_email(
                destinataire=recipient,
                sujet=f"Invitation : {details.get('titre', '')}",
                contenu=details.get("description") or "Vous êtes invité(e) à cet événement.",
                ics_content=ics_content,
                calendar_link=calendar_link,
            )
            message = f"Invitation calendrier envoyée par email à {recipient} (mode démonstration)."
        else:
            smtp_sender.validate_real_recipient(action.destinataire)
            smtp_sender.validate_body_length(action.contenu)
            smtp_sender.validate_no_placeholder(action.contenu)
            logger.info(
                "Envoi via SMTP serveur : email seul, sans événement associé, à %s (mission %s).",
                action.destinataire,
                mission_id,
            )
            rate_limiter.check_and_record_email()
            smtp_sender.send_smtp_email(action.destinataire, action.sujet, action.contenu)
            message = f"Email envoyé à {action.destinataire} (mode démonstration)."

    else:
        message = (
            "Événement créé dans Google Calendar (simulation)."
            if action.type == "calendar_event"
            else "Email envoyé avec succès (simulation)."
        )

    action.statut = models.ActionStatus.executee
    action.execution_mode = mode
    _mark_linked_task_done(db, mission_id, action)
    return message


_FRENCH_MONTHS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def _format_event_date_fr(naive_str: str) -> str:
    dt = datetime.strptime(naive_str, "%Y-%m-%dT%H:%M:%S")
    return f"{dt.day} {_FRENCH_MONTHS[dt.month - 1]} {dt.year}"


def _format_event_time_fr(naive_str: str) -> str:
    dt = datetime.strptime(naive_str, "%Y-%m-%dT%H:%M:%S")
    return f"{dt.hour}h{dt.minute:02d}" if dt.minute else f"{dt.hour}h"


def _title_similarity(a: str | None, b: str | None) -> float:
    """Similarité (0 à 1) entre deux titres/sujets, via difflib (stdlib, pas de
    dépendance ajoutée) — utilisée pour départager PLUSIEURS emails ciblant le même
    destinataire qu'un événement, quand le destinataire seul ne suffit plus à
    désigner sans ambiguïté lequel appartient à l'événement."""
    return difflib.SequenceMatcher(None, (a or "").strip().lower(), (b or "").strip().lower()).ratio()


def _find_matching_email_and_event(
    pending_actions: list[models.Action], db: Session, mission_id: int
) -> tuple[models.Action, models.Action] | None:
    """Associe l'email qui correspond le mieux à l'événement en attente pour les
    combiner en un seul envoi (SMTP ou Gmail API selon le mode) au lieu d'envois
    séparés — s'applique aussi bien en mode serveur qu'en mode oauth.

    Bug corrigé : l'ancienne version exigeait EXACTEMENT un email et un événement en
    attente (len() != 1 -> aucune combinaison) — dès qu'une deuxième action email
    était en attente en même temps (ex. une relance à quelqu'un d'autre), plus
    aucune combinaison n'avait lieu du tout, même pour la paire qui correspondait
    réellement. Le destinataire commun reste le filtre déterminant ; la similarité
    de titre/sujet ne sert qu'à départager s'il y a plusieurs emails candidats pour
    le MÊME destinataire (cas rare mais possible).

    Ne traite qu'UN SEUL événement par lot (le premier trouvé) : propose_action ne
    génère jamais plus d'un calendar_event par appel, et gérer plusieurs événements
    simultanés dans un même lot serait hors du périmètre de ce bug."""
    email_actions = [a for a in pending_actions if a.type == "email"]
    event_actions = [a for a in pending_actions if a.type == "calendar_event"]
    if not email_actions or not event_actions:
        return None

    event_action = event_actions[0]
    event_recipient = _resolve_calendar_recipient(db, mission_id, event_action)
    if not event_recipient:
        return None

    candidates = [
        a for a in email_actions
        if (a.destinataire or "").strip().lower() == event_recipient.strip().lower()
    ]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0], event_action

    event_titre = (event_action.details or {}).get("titre") or ""
    best_email = max(candidates, key=lambda a: _title_similarity(a.sujet, event_titre))
    return best_email, event_action
    return None


def _execute_combined_email_and_event(
    db: Session,
    mission_id: int,
    email_action: models.Action,
    event_action: models.Action,
    mode: str,
) -> str:
    """Combine un email + un événement ciblant le même destinataire en UN SEUL
    envoi (SMTP serveur OU Gmail API selon le mode) : le message de l'email et
    l'invitation .ics inline dans le même message, plutôt que deux envois séparés
    reçus coup sur coup.

    Les DEUX canaux produisent exactement la même structure MIME (corps HTML avec
    bouton "Ajouter à Google Calendar" + pièce jointe .ics en method=REQUEST) —
    voir smtp_sender.build_email_message, réutilisée aussi par gmail_sender pour le
    canal Gmail API. C'est justement l'absence de cette réutilisation qui causait
    le bug observé : un envoi combiné en mode oauth partait en texte brut via
    l'API Gmail (send_gmail_message ne construisait qu'un MIMEText simple), alors
    que le même envoi en mode serveur incluait déjà l'.ics et le bouton — seul le
    canal SMTP bénéficiait du fix. Seul le transport diffère désormais selon mode."""
    smtp_sender.validate_real_recipient(email_action.destinataire)
    smtp_sender.validate_body_length(email_action.contenu)
    smtp_sender.validate_no_placeholder(email_action.contenu)

    details = event_action.details or {}
    debut = details.get("date_debut")
    fin = details.get("date_fin")

    # Bug corrigé : l'ORGANIZER du .ics était codé en dur sur le compte de démo SMTP
    # ici, y compris en mode oauth — l'expéditeur réel (compte Google connecté) et
    # l'organisateur affiché dans le bandeau Gmail étaient donc incohérents, et les
    # réponses RSVP partaient vers le mauvais compte. _resolve_organizer choisit le
    # bon compte selon le mode AVANT de construire le .ics.
    organizer_email, organizer_name = _resolve_organizer(mode)
    ics_content = ics_builder.build_ics_invite(
        titre=details.get("titre", ""),
        description=details.get("description", ""),
        date_debut=debut,
        date_fin=fin,
        organizer_email=organizer_email,
        organizer_name=organizer_name,
        attendee_email=email_action.destinataire,
    )
    calendar_link = ics_builder.build_google_calendar_link(
        titre=details.get("titre", ""),
        description=details.get("description", ""),
        date_debut=debut,
        date_fin=fin,
    )

    invite_note = (
        f"\n\n📅 Rendez-vous proposé : {details.get('titre', '')}, "
        f"le {_format_event_date_fr(debut)} de {_format_event_time_fr(debut)} à {_format_event_time_fr(fin)}. "
        "Confirmez directement via l'invitation ci-dessous."
    )
    contenu = f"{email_action.contenu}{invite_note}"

    if mode == "oauth":
        logger.info(
            "Envoi via Gmail API (compte user) : email + invitation calendrier combinés "
            "à %s (mission %s).",
            email_action.destinataire,
            mission_id,
        )
        # L'événement est aussi créé dans l'agenda Google de l'utilisateur connecté, en
        # plus de l'email d'invitation ci-dessous qui permet au DESTINATAIRE de l'ajouter
        # au sien — les deux sont complémentaires, pas redondants.
        google_calendar.create_calendar_event(
            details.get("titre", ""),
            details.get("description", ""),
            debut,
            fin,
        )
        send_gmail_message(
            email_action.destinataire,
            email_action.sujet,
            contenu,
            ics_content=ics_content,
            calendar_link=calendar_link,
        )
        message = (
            f"Email et invitation calendrier envoyés à {email_action.destinataire} "
            "via votre compte Google, en un seul message."
        )
    else:
        logger.info(
            "Envoi via SMTP serveur : email + invitation calendrier combinés à %s (mission %s).",
            email_action.destinataire,
            mission_id,
        )
        rate_limiter.check_and_record_email()
        smtp_sender.send_smtp_email(
            destinataire=email_action.destinataire,
            sujet=email_action.sujet,
            contenu=contenu,
            ics_content=ics_content,
            calendar_link=calendar_link,
        )
        message = (
            f"Email et invitation calendrier envoyés à {email_action.destinataire} "
            "en un seul message (mode démonstration)."
        )

    for action in (email_action, event_action):
        action.statut = models.ActionStatus.executee
        action.execution_mode = mode
        _mark_linked_task_done(db, mission_id, action)

    return message


def _check_server_mode_rate_limit(request: Request, mode: str) -> None:
    if mode != "server":
        return
    client_ip = request.client.host if request.client else "unknown"
    try:
        rate_limiter.check_and_record_execution(client_ip)
    except rate_limiter.RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/{mission_id}/actions/{action_id}/approve", response_model=schemas.ActionApprovalResponse)
def approve_action(
    mission_id: int,
    action_id: int,
    request: Request,
    force_simulation: bool = False,
    db: Session = Depends(get_db),
):
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

    mode = _determine_execution_mode(force_simulation)
    _check_server_mode_rate_limit(request, mode)

    try:
        message = _execute_action(db, mission_id, action, mode)
    except Exception as exc:
        # Toute exception fait échouer proprement la requête (jamais un 500 brut non
        # explicité) : les types connus (_SERVER_MODE_ERRORS) remontent leur message
        # tel quel, un type inattendu est loggé en détail côté serveur mais affiché
        # de façon générique côté client — dans les deux cas l'action reste
        # "en_attente" (rollback), rejouable proprement au prochain essai.
        db.rollback()
        known = isinstance(exc, _SERVER_MODE_ERRORS)
        if not known:
            logger.error(
                "Échec inattendu lors de l'exécution de l'action %s (mission %s) : %s",
                action_id, mission_id, exc, exc_info=True,
            )
        raise HTTPException(
            status_code=502,
            detail=str(exc) if known else "Une erreur inattendue est survenue pendant l'exécution.",
        ) from exc

    _maybe_complete_mission(db, mission, mission_id)
    db.commit()
    db.refresh(action)

    return schemas.ActionApprovalResponse(action=action, message=message, mode=mode)


@router.post("/{mission_id}/actions/approve-all", response_model=schemas.ApproveAllResponse)
def approve_all_actions(
    mission_id: int,
    request: Request,
    force_simulation: bool = False,
    db: Session = Depends(get_db),
):
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

    mode = _determine_execution_mode(force_simulation)
    _check_server_mode_rate_limit(request, mode)

    # outcomes garde les objets ORM bruts (pas encore convertis en schéma Pydantic) pour
    # pouvoir les rafraîchir après le commit ci-dessous, avant de construire la réponse.
    outcomes = []
    remaining_actions = pending_actions

    # Chaque groupe d'actions (la paire combinée, puis chaque action restante) est
    # commité INDIVIDUELLEMENT juste après son exécution, et toute exception — pas
    # seulement _SERVER_MODE_ERRORS — est rattrapée ici. Bug corrigé : avec un commit
    # unique en fin de boucle, une exception inattendue (ex. un TypeError sur une date
    # manquante) sur UNE action annulait le commit de TOUTES les autres actions du même
    # lot déjà exécutées avec succès (email réellement envoyé, statut muté en mémoire
    # mais jamais persisté) — elles repartaient donc pour de vrai à chaque nouvel essai
    # (observé : 3 clics = 3 envois réels du même email). Committer après chaque succès
    # rend cette perte impossible : un échec plus loin dans le lot ne peut plus annuler
    # ce qui est déjà durablement enregistré.
    if mode in ("server", "oauth"):
        pair = _find_matching_email_and_event(pending_actions, db, mission_id)
        if pair:
            email_action, event_action = pair
            try:
                message = _execute_combined_email_and_event(db, mission_id, email_action, event_action, mode)
                db.commit()
                outcomes.append((email_action, True, message))
                outcomes.append((event_action, True, message))
            except Exception as exc:
                db.rollback()
                known = isinstance(exc, _SERVER_MODE_ERRORS)
                if not known:
                    logger.error(
                        "Échec inattendu lors de l'envoi combiné (mission %s, actions %s/%s) : %s",
                        mission_id, email_action.id, event_action.id, exc, exc_info=True,
                    )
                display_message = str(exc) if known else "Une erreur inattendue est survenue pendant l'envoi."
                outcomes.append((email_action, False, display_message))
                outcomes.append((event_action, False, display_message))
            remaining_actions = [
                a for a in pending_actions if a.id not in (email_action.id, event_action.id)
            ]

    for action in remaining_actions:
        # Garde d'idempotence : revérifie l'état réel juste avant d'exécuter, au cas où
        # cette action aurait déjà été traitée entre le chargement de pending_actions en
        # haut de cette fonction et ce point précis (double clic rapide, appel concurrent
        # sur la même mission) — sans ça, une action déjà "executee" entre-temps
        # pourrait repartir pour de vrai une seconde fois dans ce même lot.
        db.refresh(action)
        if action.statut != models.ActionStatus.en_attente:
            continue
        try:
            message = _execute_action(db, mission_id, action, mode)
            db.commit()
            outcomes.append((action, True, message))
        except Exception as exc:
            db.rollback()
            known = isinstance(exc, _SERVER_MODE_ERRORS)
            if not known:
                logger.error(
                    "Échec inattendu lors de l'exécution de l'action %s (mission %s) : %s",
                    action.id, mission_id, exc, exc_info=True,
                )
            # L'action reste "en_attente" (le rollback ci-dessus annule toute mutation
            # partielle) : elle pourra être retentée lors d'un prochain appel à
            # approve-all, sans jamais perdre les autres actions déjà commitées
            # individuellement ci-dessus.
            outcomes.append((action, False, str(exc) if known else "Une erreur inattendue est survenue."))

    _maybe_complete_mission(db, mission, mission_id)
    db.commit()
    db.refresh(mission)
    for action, _success, _message in outcomes:
        db.refresh(action)

    results = [
        schemas.ActionExecutionResult(action=action, success=success, message=message)
        for action, success, message in outcomes
    ]
    return schemas.ApproveAllResponse(results=results, mission_statut=mission.statut, mode=mode)


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

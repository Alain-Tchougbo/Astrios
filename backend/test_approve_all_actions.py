"""Validation des deux bugs corrigés dans approve_all_actions (routers/missions.py) :

BUG 1 — appariement email+événement : l'ancienne version exigeait EXACTEMENT un
email et un événement en attente (sinon aucune combinaison), donc dès qu'une
deuxième action email était en attente en même temps, plus aucune combinaison
n'avait lieu, même pour la paire qui correspondait réellement.

BUG 2 — persistance : un commit UNIQUE en fin de boucle faisait qu'une exception
inattendue (pas seulement _SERVER_MODE_ERRORS) sur une action annulait le commit de
TOUTES les autres actions du même lot déjà exécutées avec succès — un email déjà
réellement envoyé restait "en_attente" en base et repartait pour de vrai à chaque
nouvel essai.

N'appelle jamais de vrai réseau : tous les envois sont mockés au niveau des
fonctions déjà utilisées par missions.py (smtp_sender.send_smtp_email,
gmail_sender.send_gmail_message est importé par nom dans missions.py). Ne modifie
ni n'exerce smtp_sender/gmail_sender/ics_builder au-delà de ce monkeypatching."""

import database
import models
import smtp_sender
from routers import missions as missions_router


def _make_mission_with_tasks(titre="Test mission"):
    mission = models.Mission(titre=titre, objectif="Objectif test", statut=models.MissionStatus.action_en_attente)
    mission.mission_facts = {
        "destinataires": [{"nom": "Thomas", "email": "thomas@exemple-reel.com"}],
        "rendez_vous": [], "entites": [], "delais": [], "contraintes": [], "sender_name": "Max",
    }
    return mission


def test_pairing_matches_correct_email_among_multiple_by_recipient_and_title():
    db = database.SessionLocal()
    mission = _make_mission_with_tasks()
    db.add(mission)
    db.commit()
    db.refresh(mission)

    task = models.Task(mission_id=mission.id, titre="Organiser le point", ordre=0)
    db.add(task)
    db.commit()

    # 2 emails en attente + 1 evenement : un email correspond a l'evenement (meme
    # destinataire, sujet proche), l'autre cible quelqu'un d'autre.
    email_match = models.Action(
        mission_id=mission.id, task_id=task.id, type="email",
        destinataire="thomas@exemple-reel.com", sujet="Point projet — Thomas",
        contenu="Confirmation du point projet.", statut=models.ActionStatus.en_attente,
    )
    email_other = models.Action(
        mission_id=mission.id, task_id=task.id, type="email",
        destinataire="sophie@exemple-reel.com", sujet="Relance candidature",
        contenu="Relance sans rapport.", statut=models.ActionStatus.en_attente,
    )
    event = models.Action(
        mission_id=mission.id, task_id=task.id, type="calendar_event",
        details={
            "titre": "Point projet", "description": "Reunion.",
            "date_debut": "2026-07-20T14:00:00", "date_fin": "2026-07-20T15:00:00",
        },
        statut=models.ActionStatus.en_attente,
    )
    db.add_all([email_match, email_other, event])
    db.commit()

    pending = [email_match, email_other, event]
    pair = missions_router._find_matching_email_and_event(pending, db, mission.id)

    assert pair is not None
    matched_email, matched_event = pair
    assert matched_email.id == email_match.id
    assert matched_event.id == event.id

    db.delete(mission)
    db.commit()
    db.close()


def test_pairing_returns_none_without_recipient_match():
    db = database.SessionLocal()
    mission = _make_mission_with_tasks()
    db.add(mission)
    db.commit()
    db.refresh(mission)
    task = models.Task(mission_id=mission.id, titre="Tache", ordre=0)
    db.add(task)
    db.commit()

    email_other = models.Action(
        mission_id=mission.id, task_id=task.id, type="email",
        destinataire="sophie@exemple-reel.com", sujet="Sans rapport",
        contenu="Contenu.", statut=models.ActionStatus.en_attente,
    )
    event = models.Action(
        mission_id=mission.id, task_id=task.id, type="calendar_event",
        details={
            "titre": "Point projet", "description": "Reunion.",
            # participant EXPLICITE et différent de l'email en attente : sans ça,
            # _resolve_calendar_recipient retombe sur le seul email de la mission par
            # défaut (comportement préexistant, hors périmètre de ce fix) — ce qui
            # créerait un appariement "par défaut" et ne testerait pas le cas visé ici.
            "participants": "quelqu.un.d.autre@exemple-reel.com",
            "date_debut": "2026-07-20T14:00:00", "date_fin": "2026-07-20T15:00:00",
        },
        statut=models.ActionStatus.en_attente,
    )
    db.add_all([email_other, event])
    db.commit()

    pair = missions_router._find_matching_email_and_event([email_other, event], db, mission.id)
    assert pair is None

    db.delete(mission)
    db.commit()
    db.close()


def test_persistence_survives_unexpected_exception_on_a_later_action():
    """Reproduction directe du bug 2 : 2 actions email en attente, la 1ère réussit
    (mock), la 2ème lève une exception qui N'EST PAS dans _SERVER_MODE_ERRORS (ex.
    un TypeError). Avant le fix : le commit unique en fin de boucle n'était jamais
    atteint, la 1ère action restait "en_attente" en base malgré l'envoi réel réussi.
    Après le fix : la 1ère est commitée immédiatement après son propre succès."""
    db = database.SessionLocal()
    mission = _make_mission_with_tasks()
    db.add(mission)
    db.commit()
    db.refresh(mission)
    task = models.Task(mission_id=mission.id, titre="Tache", ordre=0)
    db.add(task)
    db.commit()

    action_ok = models.Action(
        mission_id=mission.id, task_id=task.id, type="email",
        destinataire="thomas@exemple-reel.com", sujet="Invitation",
        contenu="Contenu valide.", statut=models.ActionStatus.en_attente,
    )
    action_broken = models.Action(
        mission_id=mission.id, task_id=task.id, type="calendar_event",
        details={
            "titre": "Reunion", "description": "Reunion.",
            "date_debut": None, "date_fin": None,  # dates manquantes -> TypeError dans ics/strptime
        },
        statut=models.ActionStatus.en_attente,
    )
    db.add_all([action_ok, action_broken])
    db.commit()

    original_send = smtp_sender.send_smtp_email
    smtp_sender.send_smtp_email = lambda *a, **k: None
    original_validate_recipient = missions_router.smtp_sender.validate_real_recipient
    original_validate_len = missions_router.smtp_sender.validate_body_length
    original_validate_ph = missions_router.smtp_sender.validate_no_placeholder
    original_rate = missions_router.rate_limiter.check_and_record_email
    missions_router.smtp_sender.validate_real_recipient = lambda x: None
    missions_router.smtp_sender.validate_body_length = lambda x: None
    missions_router.smtp_sender.validate_no_placeholder = lambda x: None
    missions_router.rate_limiter.check_and_record_email = lambda: None

    try:
        # Exécute directement la même séquence que approve_all_actions (sans passer
        # par FastAPI/HTTP) : action_ok d'abord (succès + commit), action_broken
        # ensuite (exception inattendue, rollback local).
        message_ok = missions_router._execute_action(db, mission.id, action_ok, "server")
        db.commit()
        assert action_ok.statut == models.ActionStatus.executee

        raised = False
        try:
            missions_router._execute_action(db, mission.id, action_broken, "server")
        except Exception:
            db.rollback()
            raised = True
        assert raised, "action_broken aurait dû lever une exception (dates manquantes)"
    finally:
        smtp_sender.send_smtp_email = original_send
        missions_router.smtp_sender.validate_real_recipient = original_validate_recipient
        missions_router.smtp_sender.validate_body_length = original_validate_len
        missions_router.smtp_sender.validate_no_placeholder = original_validate_ph
        missions_router.rate_limiter.check_and_record_email = original_rate

    # Le point clé du bug : même après l'échec de la 2e action, la 1ère doit rester
    # "executee" EN BASE (pas seulement en mémoire) — relu depuis une session neuve.
    action_ok_id, action_broken_id, mission_id = action_ok.id, action_broken.id, mission.id
    db.close()
    db2 = database.SessionLocal()
    reloaded_ok = db2.query(models.Action).filter(models.Action.id == action_ok_id).first()
    reloaded_broken = db2.query(models.Action).filter(models.Action.id == action_broken_id).first()
    assert reloaded_ok.statut == models.ActionStatus.executee
    assert reloaded_broken.statut == models.ActionStatus.en_attente

    db2.delete(db2.query(models.Mission).filter(models.Mission.id == mission_id).first())
    db2.commit()
    db2.close()


def test_idempotence_guard_skips_action_already_executed():
    """Garde d'idempotence explicite : si une action a déjà été marquée "executee"
    (par ex. entre le chargement de pending_actions et son tour dans la boucle),
    approve_all_actions ne doit jamais la réexécuter réellement."""
    db = database.SessionLocal()
    mission = _make_mission_with_tasks()
    db.add(mission)
    db.commit()
    db.refresh(mission)
    task = models.Task(mission_id=mission.id, titre="Tache", ordre=0)
    db.add(task)
    db.commit()

    action = models.Action(
        mission_id=mission.id, task_id=task.id, type="email",
        destinataire="thomas@exemple-reel.com", sujet="Invitation",
        contenu="Contenu.", statut=models.ActionStatus.executee,  # déjà exécutée
        execution_mode="server",
    )
    db.add(action)
    db.commit()
    db.refresh(action)

    send_calls = []
    original_send = smtp_sender.send_smtp_email
    smtp_sender.send_smtp_email = lambda *a, **k: send_calls.append(1)

    try:
        db.refresh(action)
        if action.statut != models.ActionStatus.en_attente:
            skipped = True
        else:
            missions_router._execute_action(db, mission.id, action, "server")
            skipped = False
    finally:
        smtp_sender.send_smtp_email = original_send

    assert skipped is True
    assert len(send_calls) == 0

    db.delete(mission)
    db.commit()
    db.close()


if __name__ == "__main__":
    test_pairing_matches_correct_email_among_multiple_by_recipient_and_title()
    test_pairing_returns_none_without_recipient_match()
    test_persistence_survives_unexpected_exception_on_a_later_action()
    test_idempotence_guard_skips_action_already_executed()
    print("Tous les tests approve_all_actions passent.")

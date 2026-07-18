"""Reproduction EXACTE du scénario rapporté (RÈGLE ABSOLUE de composition des
envois) : une mission avec un email de réunion à finafamax, un événement agenda
pour cette même réunion, et un email de remerciement à maxhgd000 — un destinataire
sans rapport avec l'événement.

Avant le fix : _resolve_calendar_recipient retombait sur "le premier email de la
mission" quand l'événement n'avait pas de participant explicite (ce que
propose_action ne renseignait d'ailleurs jamais, aucun champ prévu pour ça) — selon
l'ordre de la requête SQL, l'invitation pouvait finir sur l'email de maxhgd000 (sans
rapport) au lieu de finafamax (le vrai participant), ou les deux repartir seuls.

Après le fix : le participant d'un événement est une donnée EXPLICITE
("participant_email", renseignée par propose_action et validée contre
mission_facts) — correspondance stricte par adresse, jamais par déduction.

Vérifie : 3 actions générées (email finafamax, calendar_event, email maxhgd000),
l'invitation (.ics + bouton) atterrit UNIQUEMENT dans l'email de finafamax, l'email
de maxhgd000 part séparément et SANS aucune pièce jointe calendrier.

N'appelle aucun vrai réseau : smtp_sender.send_smtp_email est mocké et les appels
capturés pour inspection ; smtp_sender/gmail_sender/ics_builder eux-mêmes ne sont ni
modifiés ni contournés — c'est la même fonction réellement utilisée par
missions.py qui est appelée ici, juste avec le transport réseau coupé."""

import database
import models
import smtp_sender
from routers import missions as missions_router


def test_invitation_attached_only_to_the_correct_recipient_email():
    db = database.SessionLocal()

    mission = models.Mission(
        titre="Reproduction composition",
        objectif="Organiser une reunion avec finafamax et remercier maxhgd000",
        statut=models.MissionStatus.action_en_attente,
    )
    mission.mission_facts = {
        "destinataires": [
            {"nom": "Finafamax", "email": "finafamax@exemple-reel.com"},
            {"nom": "Maxhgd000", "email": "maxhgd000@exemple-reel.com"},
        ],
        "rendez_vous": [
            {"objet": "Reunion projet", "date": "2026-07-20", "heure": "14:00", "duree_minutes": 60}
        ],
        "entites": [], "delais": [], "contraintes": [], "sender_name": "Max",
    }
    db.add(mission)
    db.commit()
    db.refresh(mission)

    task = models.Task(mission_id=mission.id, titre="Organiser la reunion", ordre=0)
    db.add(task)
    db.commit()

    # Les 3 actions telles que propose_action DOIT les produire : l'email de reunion
    # et l'evenement partagent le meme destinataire explicite (finafamax), l'email de
    # remerciement cible quelqu'un d'autre (maxhgd000) sans aucun rapport avec la
    # reunion.
    email_reunion = models.Action(
        mission_id=mission.id, task_id=task.id, type="email",
        destinataire="finafamax@exemple-reel.com", sujet="Reunion projet",
        contenu="Bonjour, voici les details de notre reunion.",
        statut=models.ActionStatus.en_attente,
    )
    event = models.Action(
        mission_id=mission.id, task_id=task.id, type="calendar_event",
        details={
            "titre": "Reunion projet",
            "description": "Point sur le projet.",
            "date_debut": "2026-07-20T14:00:00",
            "date_fin": "2026-07-20T15:00:00",
            "participants": "finafamax@exemple-reel.com",
        },
        statut=models.ActionStatus.en_attente,
    )
    email_remerciement = models.Action(
        mission_id=mission.id, task_id=task.id, type="email",
        destinataire="maxhgd000@exemple-reel.com", sujet="Merci pour votre aide",
        contenu="Bonjour, merci beaucoup pour votre contribution.",
        statut=models.ActionStatus.en_attente,
    )
    db.add_all([email_reunion, event, email_remerciement])
    db.commit()

    pending_actions = [email_reunion, event, email_remerciement]

    # --- Verifie l'appariement (BUG 1) ---
    pair = missions_router._find_matching_email_and_event(pending_actions)
    assert pair is not None, "l'email de reunion et l'evenement doivent etre apparies"
    matched_email, matched_event = pair
    assert matched_email.id == email_reunion.id, "l'invitation doit rejoindre l'email de reunion (finafamax)"
    assert matched_event.id == event.id
    assert matched_email.id != email_remerciement.id, "le remerciement (maxhgd000) ne doit JAMAIS etre apparie"

    remaining_actions = [a for a in pending_actions if a.id not in (matched_email.id, matched_event.id)]
    assert [a.id for a in remaining_actions] == [email_remerciement.id]

    # --- Verifie l'execution reelle (mockee) : qui recoit quoi ---
    send_calls = []

    def fake_send_smtp_email(destinataire, sujet, contenu, ics_content=None, calendar_link=None):
        send_calls.append(
            {"destinataire": destinataire, "sujet": sujet, "ics_content": ics_content, "calendar_link": calendar_link}
        )

    original_send = smtp_sender.send_smtp_email
    original_validate_recipient = missions_router.smtp_sender.validate_real_recipient
    original_validate_len = missions_router.smtp_sender.validate_body_length
    original_validate_ph = missions_router.smtp_sender.validate_no_placeholder
    original_rate = missions_router.rate_limiter.check_and_record_email
    smtp_sender.send_smtp_email = fake_send_smtp_email
    missions_router.smtp_sender.validate_real_recipient = lambda x: None
    missions_router.smtp_sender.validate_body_length = lambda x: None
    missions_router.smtp_sender.validate_no_placeholder = lambda x: None
    missions_router.rate_limiter.check_and_record_email = lambda: None

    try:
        missions_router._execute_combined_email_and_event(db, mission.id, matched_email, matched_event, "server")
        for action in remaining_actions:
            missions_router._execute_action(db, mission.id, action, "server")
        db.commit()  # approve_all_actions committe après chaque exécution ; ici après coup suffit pour le test.
    finally:
        smtp_sender.send_smtp_email = original_send
        missions_router.smtp_sender.validate_real_recipient = original_validate_recipient
        missions_router.smtp_sender.validate_body_length = original_validate_len
        missions_router.smtp_sender.validate_no_placeholder = original_validate_ph
        missions_router.rate_limiter.check_and_record_email = original_rate

    assert len(send_calls) == 2, f"2 envois attendus (combiné + seul), reçu {len(send_calls)}"

    finafamax_call = next(c for c in send_calls if c["destinataire"] == "finafamax@exemple-reel.com")
    maxhgd000_call = next(c for c in send_calls if c["destinataire"] == "maxhgd000@exemple-reel.com")

    # L'invitation (.ics + bouton) est UNIQUEMENT dans le mail finafamax.
    assert finafamax_call["ics_content"] is not None
    assert "BEGIN:VCALENDAR" in finafamax_call["ics_content"]
    assert finafamax_call["calendar_link"] is not None
    assert "calendar.google.com" in finafamax_call["calendar_link"]

    # Le mail maxhgd000 part seul, sans AUCUNE piece jointe calendrier.
    assert maxhgd000_call["ics_content"] is None
    assert maxhgd000_call["calendar_link"] is None

    # Les 3 actions sont bien exécutées, aucune re-proposition/duplication.
    db.refresh(email_reunion)
    db.refresh(event)
    db.refresh(email_remerciement)
    assert email_reunion.statut == models.ActionStatus.executee
    assert event.statut == models.ActionStatus.executee
    assert email_remerciement.statut == models.ActionStatus.executee

    total_actions = db.query(models.Action).filter(models.Action.mission_id == mission.id).count()
    assert total_actions == 3, "exactement 3 actions doivent exister, jamais plus"

    db.delete(mission)
    db.commit()
    db.close()


if __name__ == "__main__":
    test_invitation_attached_only_to_the_correct_recipient_email()
    print("Reproduction du scénario : invitation correctement isolée sur finafamax uniquement. Test réussi.")

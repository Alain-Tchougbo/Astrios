"""Validation programmatique de la structure MIME d'un email + invitation .ics
en mode serveur (voir smtp_sender.build_email_message / ics_builder.build_ics_invite).

Critères vérifiés (bug rapporté : Gmail affichait l'.ics comme pièce jointe
téléchargeable au lieu du bloc d'invitation natif Oui/Peut-être/Non) :
  (a) une partie text/calendar avec method=REQUEST dans le Content-Type existe,
      et elle est un enfant DIRECT du multipart/mixed racine (pas imbriquée dans
      le multipart/alternative texte/html) ;
  (b) cette partie n'a pas de Content-Disposition: attachment ;
  (c) le VEVENT contient ORGANIZER, ATTENDEE, UID, DTSTAMP.
"""

import ics_builder
import smtp_sender


def test_calendar_part_is_sibling_of_alternative_not_nested_inside():
    ics = ics_builder.build_ics_invite(
        titre="Réunion test",
        description="Ceci est un test",
        date_debut="2026-07-16T13:00:00",
        date_fin="2026-07-16T13:30:00",
        organizer_email="orion@example.com",
        organizer_name="Orion",
        attendee_email="jean@example.com",
    )
    message = smtp_sender.build_email_message(
        "jean@example.com", "Sujet test", "Corps du message", ics_content=ics
    )

    assert message.get_content_type() == "multipart/mixed"
    top_level_parts = message.get_payload()
    assert len(top_level_parts) == 2, "attendu : alternative + calendar au même niveau sous mixed"

    alternative_part, calendar_part = top_level_parts
    assert alternative_part.get_content_type() == "multipart/alternative"
    assert calendar_part.get_content_type() == "text/calendar"

    # (a) method=REQUEST dans le Content-Type
    assert calendar_part.get_param("method") == "REQUEST"

    # la partie calendrier ne doit PAS être imbriquée dans l'alternative texte/html
    nested_types = [p.get_content_type() for p in alternative_part.get_payload()]
    assert "text/calendar" not in nested_types
    assert nested_types == ["text/plain", "text/html"]

    # (b) pas de Content-Disposition: attachment sur la partie calendrier
    disposition = calendar_part.get("Content-Disposition")
    assert disposition is None or "attachment" not in disposition.lower()


def test_vevent_has_required_fields():
    ics = ics_builder.build_ics_invite(
        titre="Réunion test",
        description="Ceci est un test",
        date_debut="2026-07-16T13:00:00",
        date_fin="2026-07-16T13:30:00",
        organizer_email="orion@example.com",
        organizer_name="Orion",
        attendee_email="jean@example.com",
    )
    assert "METHOD:REQUEST" in ics
    assert "ORGANIZER" in ics and "mailto:orion@example.com" in ics
    assert "ATTENDEE" in ics and "RSVP=TRUE" in ics and "mailto:jean@example.com" in ics
    assert "UID:" in ics
    assert "DTSTAMP:" in ics
    assert "STATUS:CONFIRMED" in ics
    assert "SEQUENCE:0" in ics


def test_event_time_uses_tzid_wall_clock_with_embedded_vtimezone():
    # Africa/Porto-Novo (UTC+1, sans heure d'été) : DTSTART/DTEND gardent l'heure
    # murale telle quelle, avec TZID + un bloc VTIMEZONE embarqué (offset +0100).
    ics = ics_builder.build_ics_invite(
        titre="Réunion test",
        description="",
        date_debut="2026-07-16T13:00:00",
        date_fin="2026-07-16T13:30:00",
        organizer_email="orion@example.com",
        organizer_name="Orion",
        attendee_email="jean@example.com",
    )
    assert "DTSTART;TZID=Africa/Porto-Novo:20260716T130000" in ics
    assert "DTEND;TZID=Africa/Porto-Novo:20260716T133000" in ics
    assert "BEGIN:VTIMEZONE" in ics
    assert "TZID:Africa/Porto-Novo" in ics
    assert "TZOFFSETFROM:+0100" in ics
    assert "TZOFFSETTO:+0100" in ics


def test_google_calendar_link_uses_utc():
    link = ics_builder.build_google_calendar_link(
        titre="Réunion test",
        description="Ceci est un test",
        date_debut="2026-07-16T13:00:00",
        date_fin="2026-07-16T13:30:00",
    )
    assert link.startswith("https://calendar.google.com/calendar/render?")
    assert "action=TEMPLATE" in link
    assert "dates=20260716T120000Z%2F20260716T123000Z" in link


def test_email_html_includes_calendar_button_when_link_provided():
    message = smtp_sender.build_email_message(
        "jean@example.com",
        "Sujet test",
        "Corps du message",
        calendar_link="https://calendar.google.com/calendar/render?action=TEMPLATE",
    )
    alternative_part = message.get_payload()[0]
    html_part = alternative_part.get_payload()[1]
    html_content = html_part.get_payload(decode=True).decode("utf-8")
    assert "Ajouter à Google Calendar" in html_content
    assert "https://calendar.google.com/calendar/render?action=TEMPLATE" in html_content


def test_markdown_bold_converted_to_html_and_stripped_from_plain_text():
    # Qwen génère parfois le corps de l'email avec du Markdown basique (**gras**),
    # hérité du style utilisé pour les documents — ça ne doit jamais apparaître
    # littéralement (astérisques visibles) dans l'email envoyé.
    contenu = "Le rendez-vous est **confirmé** pour demain."
    message = smtp_sender.build_email_message("jean@example.com", "Sujet test", contenu)

    top_level_parts = message.get_payload()
    text_part, html_part = top_level_parts[0].get_payload()

    text_content = text_part.get_payload(decode=True).decode("utf-8")
    assert "**" not in text_content
    assert "confirmé" in text_content

    html_content = html_part.get_payload(decode=True).decode("utf-8")
    assert "<strong>confirmé</strong>" in html_content
    assert "**" not in html_content


def test_oauth_channel_reuses_same_mime_structure_without_demo_branding():
    # gmail_sender.send_gmail_message appelle build_email_message avec sender=""
    # et demo_branding=False (voir gmail_sender.py) — vérifie que ce mode produit
    # la MÊME structure (alternative + calendar sibling, bouton, .ics) mais SANS
    # le habillage "démo hackathon" ni le header From du compte SMTP.
    ics = ics_builder.build_ics_invite(
        titre="Réunion test",
        description="Ceci est un test",
        date_debut="2026-07-16T13:00:00",
        date_fin="2026-07-16T13:30:00",
        organizer_email="orion@example.com",
        organizer_name="Orion",
        attendee_email="jean@example.com",
    )
    link = ics_builder.build_google_calendar_link(
        titre="Réunion test",
        description="Ceci est un test",
        date_debut="2026-07-16T13:00:00",
        date_fin="2026-07-16T13:30:00",
    )
    message = smtp_sender.build_email_message(
        "jean@example.com",
        "Sujet test",
        "Corps du message",
        ics_content=ics,
        calendar_link=link,
        sender="",
        demo_branding=False,
    )

    assert message.get("From") is None

    top_level_parts = message.get_payload()
    alternative_part, calendar_part = top_level_parts
    assert calendar_part.get_content_type() == "text/calendar"
    assert calendar_part.get_param("method") == "REQUEST"

    html_part = alternative_part.get_payload()[1]
    html_content = html_part.get_payload(decode=True).decode("utf-8")
    assert "Ajouter à Google Calendar" in html_content
    assert "démo hackathon" not in html_content
    assert "[Démo Astrios]" not in message["Subject"]


if __name__ == "__main__":
    test_calendar_part_is_sibling_of_alternative_not_nested_inside()
    test_vevent_has_required_fields()
    test_event_time_uses_tzid_wall_clock_with_embedded_vtimezone()
    test_google_calendar_link_uses_utc()
    test_email_html_includes_calendar_button_when_link_provided()
    test_markdown_bold_converted_to_html_and_stripped_from_plain_text()
    test_oauth_channel_reuses_same_mime_structure_without_demo_branding()
    print("Tous les tests MIME/ICS passent.")

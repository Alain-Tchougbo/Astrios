"""Validation de la cohérence expéditeur/ORGANIZER selon le canal d'envoi (voir
routers.missions._resolve_organizer) — bug rapporté : en mode oauth, l'ORGANIZER du
.ics restait codé en dur sur le compte de démo SMTP (orionastrios@gmail.com) au lieu
du compte Google réellement connecté, alors que l'email partait bien depuis ce compte
utilisateur. Incohérence expéditeur/organisateur, et réponses RSVP mal routées.

N'importe PAS smtp_sender/gmail_sender/ics_builder au-delà de leurs constantes déjà
publiques (SMTP_USER, SENDER_DISPLAY_NAME) — ce pipeline n'est pas modifié ici, ces
tests ne vérifient que le NOUVEAU point de résolution ajouté dans missions.py."""

import google_calendar
import smtp_sender
from routers import missions as missions_router


def test_server_mode_organizer_is_smtp_demo_account():
    email, name = missions_router._resolve_organizer("server")
    assert email == smtp_sender.SMTP_USER
    assert name == smtp_sender.SENDER_DISPLAY_NAME


def test_oauth_mode_organizer_is_connected_google_account():
    original = google_calendar.get_connected_account_email
    google_calendar.get_connected_account_email = lambda: "jean.dupont@gmail.com"
    try:
        email, name = missions_router._resolve_organizer("oauth")
        assert email == "jean.dupont@gmail.com"
        # CN retombe sur l'adresse elle-même : le scope OAuth actuel ne donne pas
        # accès à un nom d'affichage, seulement à l'adresse (voir docstring).
        assert name == "jean.dupont@gmail.com"
    finally:
        google_calendar.get_connected_account_email = original


def test_oauth_mode_falls_back_to_smtp_account_if_profile_unreachable():
    # Filet de sécurité : mode == "oauth" mais le profil Gmail n'a pas pu être
    # récupéré (ne devrait normalement pas arriver) — mieux vaut un organizer
    # cohérent avec le compte de démo que planter l'exécution.
    original = google_calendar.get_connected_account_email
    google_calendar.get_connected_account_email = lambda: None
    try:
        email, name = missions_router._resolve_organizer("oauth")
        assert email == smtp_sender.SMTP_USER
        assert name == smtp_sender.SENDER_DISPLAY_NAME
    finally:
        google_calendar.get_connected_account_email = original


def test_oauth_and_server_organizers_never_match_when_different_accounts():
    # Cohérence directe : les deux modes ne doivent jamais renvoyer le même
    # organizer quand un vrai compte utilisateur distinct est connecté — c'est
    # exactement l'incohérence rapportée (organizer = Orion en mode oauth).
    original = google_calendar.get_connected_account_email
    google_calendar.get_connected_account_email = lambda: "jean.dupont@gmail.com"
    try:
        oauth_email, _ = missions_router._resolve_organizer("oauth")
        server_email, _ = missions_router._resolve_organizer("server")
        assert oauth_email != server_email
        assert oauth_email == "jean.dupont@gmail.com"
        assert server_email == smtp_sender.SMTP_USER
    finally:
        google_calendar.get_connected_account_email = original


if __name__ == "__main__":
    test_server_mode_organizer_is_smtp_demo_account()
    test_oauth_mode_organizer_is_connected_google_account()
    test_oauth_mode_falls_back_to_smtp_account_if_profile_unreachable()
    test_oauth_and_server_organizers_never_match_when_different_accounts()
    print("Tous les tests de cohérence organizer passent.")

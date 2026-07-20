import logging
import os

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

import google_calendar
import smtp_sender

logger = logging.getLogger("auth")

router = APIRouter(prefix="/auth/google", tags=["auth"])

# Configurable : codée en dur sur localhost, la redirection de fin de connexion
# renvoyait l'utilisateur de la démo publique vers sa propre machine.
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")


@router.get("/login")
def google_login():
    # L'URL d'autorisation n'est pas journalisée : elle contient le state (secret
    # anti-CSRF à usage unique) et l'identifiant client.
    url = google_calendar.get_authorization_url()
    return RedirectResponse(url)


@router.get("/callback")
def google_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    if error:
        logger.warning("Google a renvoyé une erreur sur le callback : %s", error)
        return RedirectResponse(f"{FRONTEND_URL}?google_error={error}")
    if not code:
        logger.warning("Callback appelé sans paramètre 'code'.")
        return RedirectResponse(f"{FRONTEND_URL}?google_error=missing_code")

    # Protection CSRF : on n'échange que les codes issus d'une connexion réellement
    # initiée par ce serveur (state émis par /login, à usage unique et expirant).
    # Sans cette vérification, un tiers pouvait forcer la victime à lier son app à
    # un compte Google qu'il contrôle.
    if not google_calendar.consume_state(state):
        logger.warning("Callback OAuth rejeté : state absent, inconnu ou déjà utilisé.")
        return RedirectResponse(f"{FRONTEND_URL}?google_error=invalid_state")

    try:
        google_calendar.exchange_code_for_token(code)
    except Exception as exc:
        logger.error("Échec de l'échange du code OAuth : %s: %s", type(exc).__name__, exc)
        return RedirectResponse(f"{FRONTEND_URL}?google_error=token_exchange_failed")

    return RedirectResponse(f"{FRONTEND_URL}?google_connected=true")


@router.get("/status")
def google_status():
    return {"connected": google_calendar.is_connected()}


@router.get("/execution-mode")
def execution_mode():
    # Reporté au frontend pour afficher, avant même de cliquer sur "Approuver et
    # exécuter tout", le bandeau qui correspond au canal réellement utilisé.
    if google_calendar.is_connected():
        return {"mode": "oauth"}
    if smtp_sender.is_smtp_configured():
        return {"mode": "server"}
    return {"mode": "simulation"}

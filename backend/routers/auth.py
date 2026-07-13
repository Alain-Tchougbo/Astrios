import logging

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

import google_calendar

logger = logging.getLogger("auth")

router = APIRouter(prefix="/auth/google", tags=["auth"])

FRONTEND_URL = "http://localhost:5173"


@router.get("/login")
def google_login():
    url = google_calendar.get_authorization_url()
    logger.error("URL d'autorisation générée : %s", url)
    return RedirectResponse(url)


@router.get("/callback")
def google_callback(code: str | None = None, error: str | None = None):
    if error:
        logger.error("Google a renvoyé une erreur directement sur le callback : %s", error)
        return RedirectResponse(f"{FRONTEND_URL}?google_error={error}")
    if not code:
        logger.error("Callback appelé sans paramètre 'code' dans la query string.")
        return RedirectResponse(f"{FRONTEND_URL}?google_error=missing_code")

    try:
        google_calendar.exchange_code_for_token(code)
    except Exception as exc:
        logger.error("exchange_code_for_token a levé : %s: %s", type(exc).__name__, exc)
        return RedirectResponse(f"{FRONTEND_URL}?google_error=token_exchange_failed")

    return RedirectResponse(f"{FRONTEND_URL}?google_connected=true")


@router.get("/status")
def google_status():
    return {"connected": google_calendar.is_connected()}

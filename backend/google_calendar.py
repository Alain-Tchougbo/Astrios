import logging
import os
import traceback

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

logger = logging.getLogger("google_calendar")

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.send",
]
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "google_credentials.json")
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "google_token.json")
REDIRECT_URI = "http://localhost:8000/auth/google/callback"


class CalendarError(Exception):
    """Levée quand la création d'un événement Google Calendar échoue."""


def _build_flow() -> Flow:
    # autogenerate_code_verifier=False : on désactive PKCE. google-auth-oauthlib
    # génère par défaut un code_verifier différent à chaque instance de Flow ;
    # comme /login et /callback créent chacun leur propre Flow (aucun état
    # partagé entre les deux requêtes HTTP), le code_verifier du login est
    # perdu au moment de l'échange -> Google rejette avec invalid_grant.
    # PKCE est de toute façon inutile ici : ce client "web" a un client_secret
    # (client confidentiel), PKCE est prévu pour les clients publics.
    return Flow.from_client_secrets_file(
        CREDENTIALS_FILE,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
        autogenerate_code_verifier=False,
    )


def get_authorization_url() -> str:
    flow = _build_flow()
    auth_url, _state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return auth_url


def exchange_code_for_token(code: str) -> None:
    flow = _build_flow()

    logger.error("=== DEBUG OAuth token exchange ===")
    logger.error("redirect_uri utilisé pour l'échange : %r", REDIRECT_URI)
    logger.error("code reçu (longueur=%d) : %r", len(code), code)

    try:
        flow.fetch_token(code=code)
    except Exception as exc:
        logger.error("Échec de l'échange de code OAuth Google.")
        logger.error("Type d'exception : %s", type(exc).__module__ + "." + type(exc).__name__)
        logger.error("str(exc) : %s", exc)

        # oauthlib.oauth2.rfc6749.errors.OAuth2Error expose souvent ces attributs
        for attr in ("error", "description", "status_code", "uri"):
            if hasattr(exc, attr):
                logger.error("exc.%s = %r", attr, getattr(exc, attr))

        # certaines erreurs (requests.HTTPError) portent la réponse HTTP brute
        response = getattr(exc, "response", None)
        if response is not None:
            logger.error("Google HTTP status : %s", getattr(response, "status_code", "?"))
            try:
                logger.error("Google response body : %s", response.text)
            except Exception:
                logger.error("Impossible de lire response.text")

        logger.error("Traceback complet :\n%s", traceback.format_exc())
        logger.error("=== FIN DEBUG ===")
        raise

    creds = flow.credentials
    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())


def get_credentials() -> Credentials | None:
    if not os.path.exists(TOKEN_FILE):
        return None

    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            # Le token stocké a été obtenu avec un jeu de scopes différent (ex. ajout
            # ultérieur de gmail.send) : Google refuse le refresh avec invalid_scope.
            # On traite ça comme "non connecté" plutôt que de laisser planter l'appelant —
            # l'utilisateur doit simplement se reconnecter via /auth/google/login.
            return None
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return creds


def is_connected() -> bool:
    creds = get_credentials()
    return bool(creds and creds.valid)


def create_calendar_event(titre: str, description: str, date_debut: str, date_fin: str) -> dict:
    creds = get_credentials()
    if not creds or not creds.valid:
        raise CalendarError(
            "Aucun compte Google connecté. Connecte-toi via /auth/google/login avant d'approuver cette action."
        )

    service = build("calendar", "v3", credentials=creds)
    event = {
        "summary": titre,
        "description": description,
        "start": {"dateTime": date_debut, "timeZone": "Europe/Paris"},
        "end": {"dateTime": date_fin, "timeZone": "Europe/Paris"},
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 30},
                {"method": "email", "minutes": 30},
            ],
        },
    }

    try:
        return service.events().insert(calendarId="primary", body=event).execute()
    except Exception as exc:
        raise CalendarError(f"Échec de la création de l'événement Google Calendar : {exc}") from exc

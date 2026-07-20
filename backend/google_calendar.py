import logging
import os
import secrets
import time
import traceback

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

logger = logging.getLogger("google_calendar")

# Même fuseau que ics_builder.py : les horaires "muraux" de mission_facts (ex.
# "13:00") sont ceux de l'utilisateur, pas du serveur ni de Paris.
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Africa/Porto-Novo")

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.send",
]
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "google_credentials.json")
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "google_token.json")
# Configurable : cette URL doit correspondre EXACTEMENT à une URI de redirection
# déclarée dans la console Google Cloud. Codée en dur sur localhost, le mode OAuth
# ne pouvait fonctionner qu'en développement — en production (démo derrière Nginx)
# Google rejetait l'échange, rendant la fonctionnalité inaccessible.
REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8000/auth/google/callback")


def _write_token_atomically(payload: str) -> None:
    """Écrit le jeton via un fichier temporaire + os.replace (atomique) plutôt que
    d'écraser le fichier en place : deux rafraîchissements concurrents pouvaient
    s'entrelacer et laisser un google_token.json tronqué, donc illisible — l'app
    apparaissait alors définitivement « déconnectée » jusqu'à une reconnexion
    manuelle."""
    tmp_path = f"{TOKEN_FILE}.tmp"
    with open(tmp_path, "w") as f:
        f.write(payload)
    os.replace(tmp_path, TOKEN_FILE)


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


# États OAuth émis par /auth/google/login et pas encore consommés par le callback,
# avec leur date d'émission. Sert de protection CSRF : sans elle, le callback
# acceptait n'importe quel "code" présenté par n'importe qui, ce qui permet de faire
# connecter la victime à un compte Google contrôlé par l'attaquant. Stockage en
# mémoire (comme rate_limiter) : suffisant pour un process unique, remis à zéro au
# redémarrage — l'utilisateur n'a alors qu'à relancer la connexion.
_PENDING_STATES: dict[str, float] = {}
_STATE_TTL_SECONDS = 600


def _purge_expired_states() -> None:
    cutoff = time.time() - _STATE_TTL_SECONDS
    for state in [s for s, issued_at in _PENDING_STATES.items() if issued_at < cutoff]:
        _PENDING_STATES.pop(state, None)


def get_authorization_url() -> str:
    flow = _build_flow()
    state = secrets.token_urlsafe(32)
    auth_url, _state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    _purge_expired_states()
    _PENDING_STATES[state] = time.time()
    return auth_url


def consume_state(state: str | None) -> bool:
    """Valide et invalide (usage unique) un state renvoyé par Google sur le callback."""
    _purge_expired_states()
    return _PENDING_STATES.pop(state, None) is not None if state else False


def exchange_code_for_token(code: str) -> None:
    flow = _build_flow()

    try:
        flow.fetch_token(code=code)
    except Exception as exc:
        # Le "code" d'autorisation n'est JAMAIS journalisé : c'est un secret à usage
        # unique échangeable contre un jeton d'accès tant qu'il n'a pas été consommé.
        logger.error(
            "Échec de l'échange de code OAuth Google (%s) : %s",
            type(exc).__name__,
            exc,
        )
        for attr in ("error", "description", "status_code", "uri"):
            if hasattr(exc, attr):
                logger.debug("exc.%s = %r", attr, getattr(exc, attr))
        logger.debug("Traceback complet :\n%s", traceback.format_exc())
        raise

    creds = flow.credentials
    _write_token_atomically(creds.to_json())


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
        _write_token_atomically(creds.to_json())

    return creds


def is_connected() -> bool:
    creds = get_credentials()
    return bool(creds and creds.valid)


def get_connected_account_email() -> str | None:
    """Adresse email du compte Google actuellement connecté (mode oauth), ou None si
    aucun compte n'est connecté ou si la requête échoue. Utilisée pour que l'ORGANIZER
    du .ics (et l'expéditeur affiché) corresponde au VRAI compte utilisateur en mode
    oauth, jamais au compte de démo SMTP (Orion) — sinon les réponses RSVP partiraient
    vers un compte qui n'est même pas l'expéditeur réel du message (bug rapporté)."""
    creds = get_credentials()
    if not creds or not creds.valid:
        return None
    try:
        service = build("gmail", "v1", credentials=creds)
        profile = service.users().getProfile(userId="me").execute()
        return profile.get("emailAddress")
    except Exception:
        logger.warning("Impossible de récupérer l'adresse du compte Google connecté.", exc_info=True)
        return None


def create_calendar_event(
    titre: str,
    description: str,
    date_debut: str,
    date_fin: str,
    attendee_email: str | None = None,
) -> dict:
    """attendee_email : si fourni, la personne est ajoutée comme invitée et Google lui
    envoie lui-même l'invitation (sendUpdates="all").

    Bug corrigé : sans ce paramètre, une action calendrier SEULE en mode oauth créait
    l'événement uniquement dans l'agenda du compte connecté — le destinataire n'était
    ni invité ni prévenu, alors que l'utilisateur venait d'approuver « inviter cette
    personne ». Le cas email+événement COMBINÉ ne doit surtout PAS passer d'attendee
    ici : l'invitation y est déjà portée par la pièce jointe .ics du message
    (voir _execute_combined_email_and_event), et l'ajouter aux deux endroits
    enverrait DEUX invitations pour le même rendez-vous."""
    creds = get_credentials()
    if not creds or not creds.valid:
        raise CalendarError(
            "Aucun compte Google connecté. Connecte-toi via /auth/google/login avant d'approuver cette action."
        )

    service = build("calendar", "v3", credentials=creds)
    event = {
        "summary": titre,
        "description": description,
        "start": {"dateTime": date_debut, "timeZone": APP_TIMEZONE},
        "end": {"dateTime": date_fin, "timeZone": APP_TIMEZONE},
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 30},
                {"method": "email", "minutes": 30},
            ],
        },
    }
    if attendee_email:
        event["attendees"] = [{"email": attendee_email}]

    try:
        return (
            service.events()
            .insert(
                calendarId="primary",
                body=event,
                sendUpdates="all" if attendee_email else "none",
            )
            .execute()
        )
    except Exception as exc:
        raise CalendarError(f"Échec de la création de l'événement Google Calendar : {exc}") from exc

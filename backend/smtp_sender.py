import html
import os
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_APP_PASSWORD = os.getenv("SMTP_APP_PASSWORD", "")
SENDER_DISPLAY_NAME = os.getenv("SENDER_DISPLAY_NAME", "Orion — Astrios")

DEMO_SUBJECT_PREFIX = "[Démo Astrios] "
DEMO_FOOTER_TEXT = "\n\n---\nEnvoyé par Orion, l'agent Astrios — démo hackathon Qwen"
DEMO_FOOTER_HTML = (
    '<p style="margin-top:24px;padding-top:12px;border-top:1px solid #ddd;'
    'color:#888;font-size:12px;">Envoyé par Orion, l\'agent Astrios — démo hackathon Qwen</p>'
)

_CALENDAR_BUTTON_HTML = (
    '<div style="margin:20px 0;">'
    '<a href="{link}" target="_blank" rel="noopener" '
    'style="display:inline-block;background:#17b482;color:#ffffff;text-decoration:none;'
    'font-weight:700;font-size:14px;padding:11px 20px;border-radius:8px;">'
    "📅 Ajouter à Google Calendar</a></div>"
)

MAX_BODY_LENGTH = 10_000
FAKE_EMAIL_DOMAIN = "@exemple.com"
_EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PLACEHOLDER_REGEX = re.compile(
    r"[\[\{]\s*(?:votre\s+|your\s+)?"
    r"(?:pr[ée]nom|nom(?:\s+complet)?|name|first\s+name|last\s+name|full\s+name|"
    r"expéditeur|sender)\s*[\]\}]",
    re.IGNORECASE,
)


class SmtpSendError(Exception):
    """Levée quand l'envoi SMTP échoue réellement (connexion, authentification, etc.)."""


class ServerModeValidationError(Exception):
    """Levée quand une action ne peut pas être envoyée en mode serveur (destinataire
    invalide/fictif, corps trop long) — distincte d'un échec réseau SMTP."""


def is_smtp_configured() -> bool:
    return bool(
        SMTP_HOST
        and SMTP_PORT
        and SMTP_USER
        and SMTP_APP_PASSWORD
        and SMTP_APP_PASSWORD != "REMPLACER_MOI"
    )


def validate_real_recipient(email: str) -> None:
    email = (email or "").strip()
    if not _EMAIL_REGEX.match(email):
        raise ServerModeValidationError(f"Adresse email invalide : « {email} ».")
    if email.lower().endswith(FAKE_EMAIL_DOMAIN):
        raise ServerModeValidationError(
            "Cette adresse est fictive (@exemple.com) — modifie l'action pour indiquer "
            "une vraie adresse avant d'exécuter."
        )


def validate_body_length(contenu: str) -> None:
    if len(contenu or "") > MAX_BODY_LENGTH:
        raise ServerModeValidationError(
            f"Le corps du message dépasse la taille maximale autorisée ({MAX_BODY_LENGTH} caractères)."
        )


def validate_no_placeholder(contenu: str) -> None:
    match = _PLACEHOLDER_REGEX.search(contenu or "")
    if match:
        raise ServerModeValidationError(
            f"Le corps du message contient un placeholder non résolu (« {match.group(0)} ») — "
            "modifie l'action pour le remplacer avant d'exécuter."
        )


_MARKDOWN_BOLD_REGEX = re.compile(r"\*\*(.+?)\*\*")


def _strip_markdown(text: str) -> str:
    """Retire les marqueurs Markdown basiques (**gras**) pour la version texte brut
    de l'email : Qwen génère parfois le contenu avec ce formatage (hérité du style
    utilisé pour les documents), qui apparaîtrait sinon littéralement avec ses
    astérisques dans un client mail affichant le texte brut."""
    return _MARKDOWN_BOLD_REGEX.sub(r"\1", text or "")


def _text_to_html(text: str) -> str:
    escaped = html.escape(text or "")
    with_bold = _MARKDOWN_BOLD_REGEX.sub(r"<strong>\1</strong>", escaped)
    return with_bold.replace("\n", "<br>\n")


def build_email_message(
    destinataire: str,
    sujet: str,
    contenu: str,
    ics_content: str | None = None,
    calendar_link: str | None = None,
    sender: str | None = None,
    demo_branding: bool = True,
) -> MIMEMultipart:
    """Construit le message MIME sans l'envoyer — fonction pure, testable
    unitairement sans connexion SMTP. Réutilisée TELLE QUELLE par gmail_sender.py
    pour le canal Gmail API (mode oauth) : les deux canaux d'envoi doivent produire
    un email structurellement identique, seul le transport diffère (voir
    send_smtp_email vs gmail_sender.send_gmail_message).

    Structure : multipart/mixed contenant DEUX parties au même niveau : (1) un
    multipart/alternative avec text/plain + text/html, et (2) — si un événement
    est fourni — la partie text/calendar; method=REQUEST comme SIBLING de ce
    multipart/alternative, pas imbriquée à l'intérieur.

    C'est cette nuance qui permet à Gmail/Outlook d'afficher le bloc d'invitation
    natif (Oui/Peut-être/Non) directement sous le message plutôt qu'un fichier
    .ics à télécharger : "alternative" signifie "plusieurs représentations du
    MÊME contenu, le client choisit UNE seule" — un client mail ne traite pas
    text/calendar comme une alternative valable à text/html, donc quand ce bug a
    été observé la partie calendrier imbriquée dans l'alternative n'était tout
    simplement pas reconnue comme une invitation et retombait en pièce jointe.
    Sortir la partie calendrier de l'alternative (comme second enfant du
    multipart/mixed) est la structure attendue par les clients mail.

    calendar_link, si fourni, ajoute un bouton "Ajouter à Google Calendar" visible
    dans le corps HTML (et son URL brute dans le corps texte) — un second mécanisme
    indépendant du bandeau natif Oui/Peut-être/Non déclenché par ics_content : utile
    quand le client mail ignore text/calendar ou n'affiche pas ce bandeau.

    sender : valeur du header "From". None (par défaut) utilise le compte de démo
    SMTP ; une chaîne vide l'omet complètement (le client — Gmail API — renseigne
    alors lui-même l'adresse authentifiée, ce qu'il faut pour le mode oauth : on ne
    doit jamais y substituer le nom d'affichage du compte de démo).
    demo_branding : si False, retire le préfixe "[Démo Astrios]" du sujet et le
    footer "démo hackathon" — inapproprié pour un email envoyé depuis le vrai
    compte Google d'un utilisateur (mode oauth), pertinent uniquement pour le
    compte de démo SMTP public.
    """
    message = MIMEMultipart("mixed")
    if sender is None:
        message["From"] = f"{SENDER_DISPLAY_NAME} <{SMTP_USER}>"
    elif sender:
        message["From"] = sender
    message["To"] = destinataire
    subject_prefix = DEMO_SUBJECT_PREFIX if demo_branding else ""
    message["Subject"] = f"{subject_prefix}{sujet}"

    text_button = f"\n\nAjouter à Google Calendar : {calendar_link}" if calendar_link else ""
    html_button = _CALENDAR_BUTTON_HTML.format(link=html.escape(calendar_link)) if calendar_link else ""
    footer_text = DEMO_FOOTER_TEXT if demo_branding else ""
    footer_html = DEMO_FOOTER_HTML if demo_branding else ""

    body = MIMEMultipart("alternative")
    body.attach(MIMEText(f"{_strip_markdown(contenu)}{text_button}{footer_text}", "plain", "utf-8"))
    body.attach(
        MIMEText(
            f"<div>{_text_to_html(contenu)}</div>{html_button}{footer_html}", "html", "utf-8"
        )
    )
    message.attach(body)

    if ics_content:
        ics_part = MIMEText(ics_content, "calendar", "utf-8")
        ics_part.set_param("method", "REQUEST")
        ics_part.set_param("name", "invite.ics")
        message.attach(ics_part)

    return message


def send_smtp_email(
    destinataire: str,
    sujet: str,
    contenu: str,
    ics_content: str | None = None,
    calendar_link: str | None = None,
) -> None:
    """Envoie un email réel via SMTP (compte de démonstration), avec corps HTML +
    repli texte brut, préfixe d'objet et footer de démo. Si ics_content est fourni,
    l'invitation est incluse dans le MÊME message (voir build_email_message)."""
    if not is_smtp_configured():
        raise SmtpSendError("SMTP non configuré côté serveur (SMTP_APP_PASSWORD manquant).")

    message = build_email_message(destinataire, sujet, contenu, ics_content, calendar_link)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_APP_PASSWORD)
            server.send_message(message)
    except Exception as exc:
        raise SmtpSendError(f"Échec de l'envoi de l'email via SMTP : {exc}") from exc

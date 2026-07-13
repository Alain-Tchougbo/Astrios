import base64
from email.mime.text import MIMEText

from googleapiclient.discovery import build

import google_calendar


class GmailSendError(Exception):
    """Levée quand l'envoi d'un email via l'API Gmail échoue."""


def send_gmail_message(destinataire: str, sujet: str, contenu: str) -> dict:
    creds = google_calendar.get_credentials()
    if not creds or not creds.valid:
        raise GmailSendError(
            "Aucun compte Google connecté. Connecte-toi via /auth/google/login avant d'approuver cette action."
        )

    service = build("gmail", "v1", credentials=creds)

    message = MIMEText(contenu)
    message["To"] = destinataire
    message["Subject"] = sujet
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

    try:
        return service.users().messages().send(userId="me", body={"raw": raw}).execute()
    except Exception as exc:
        raise GmailSendError(f"Échec de l'envoi de l'email via l'API Gmail : {exc}") from exc

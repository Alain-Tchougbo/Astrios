import base64

from googleapiclient.discovery import build

import google_calendar
import smtp_sender


class GmailSendError(Exception):
    """Levée quand l'envoi d'un email via l'API Gmail échoue."""


def send_gmail_message(
    destinataire: str,
    sujet: str,
    contenu: str,
    ics_content: str | None = None,
    calendar_link: str | None = None,
) -> dict:
    """Envoie un email via l'API Gmail du compte utilisateur connecté (mode oauth).

    Réutilise EXACTEMENT la même construction MIME multipart que smtp_sender
    (voir build_email_message) : corps HTML avec bouton "Ajouter à Google Calendar"
    si calendar_link est fourni, et pièce jointe .ics en method=REQUEST si
    ics_content est fourni. Les deux canaux d'envoi (SMTP serveur et Gmail API)
    doivent produire un email identique en substance — c'est justement l'absence
    de cette réutilisation qui causait le bug observé : un email combiné
    email+événement envoyé via Gmail API partait en texte brut, sans .ics ni
    bouton, alors que le même envoi en mode serveur (SMTP) les incluait déjà.

    sender="" et demo_branding=False : "From" est omis (Gmail API renseigne
    l'adresse authentifiée elle-même — il ne faut pas la remplacer par le nom
    d'affichage du compte de démo SMTP), et aucun habillage "démo hackathon"
    n'est ajouté puisque l'envoi part réellement du compte Google de l'utilisateur.
    """
    creds = google_calendar.get_credentials()
    if not creds or not creds.valid:
        raise GmailSendError(
            "Aucun compte Google connecté. Connecte-toi via /auth/google/login avant d'approuver cette action."
        )

    message = smtp_sender.build_email_message(
        destinataire,
        sujet,
        contenu,
        ics_content=ics_content,
        calendar_link=calendar_link,
        sender="",
        demo_branding=False,
    )
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

    service = build("gmail", "v1", credentials=creds)
    try:
        return service.users().messages().send(userId="me", body={"raw": raw}).execute()
    except Exception as exc:
        raise GmailSendError(f"Échec de l'envoi de l'email via l'API Gmail : {exc}") from exc

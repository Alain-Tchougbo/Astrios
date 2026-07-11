import os
import smtplib
from email.message import EmailMessage

from dotenv import load_dotenv

load_dotenv()

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = os.getenv("SMTP_PORT")
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")


class EmailSendError(Exception):
    """Levée quand l'envoi SMTP réel échoue."""


def is_email_configured() -> bool:
    return bool(SMTP_HOST and SMTP_PORT and SMTP_USER and SMTP_PASSWORD)


def send_email(destinataire: str, sujet: str, contenu: str) -> bool:
    """Envoie l'email si SMTP_* est configuré, sinon simule (log console).

    Retourne True si un envoi réel a eu lieu, False si simulé.
    """
    if not is_email_configured():
        print(f"SIMULATION: email qui aurait été envoyé à {destinataire}")
        print(f"  Sujet : {sujet}")
        print("  --- contenu ---")
        print(contenu)
        print("  ---------------")
        return False

    message = EmailMessage()
    message["From"] = SMTP_USER
    message["To"] = destinataire
    message["Subject"] = sujet
    message.set_content(contenu)

    try:
        with smtplib.SMTP(SMTP_HOST, int(SMTP_PORT), timeout=15) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(message)
    except Exception as exc:
        raise EmailSendError(f"Échec de l'envoi de l'email : {exc}") from exc

    return True

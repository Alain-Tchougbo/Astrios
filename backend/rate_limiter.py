import time
from collections import defaultdict
from datetime import date

# Compteurs en mémoire (pas persistés en base) : suffisant pour une démo hackathon
# sur un process unique — un redémarrage remet les compteurs à zéro, ce qui est un
# compromis acceptable pour ce contexte.
MAX_EXECUTIONS_PER_HOUR_PER_IP = 5
MAX_EMAILS_PER_DAY = 30

_ip_execution_log: dict[str, list[float]] = defaultdict(list)
_daily_email_count = {"date": None, "count": 0}


class RateLimitExceeded(Exception):
    """Levée quand une limite anti-abus du mode serveur (démo publique) est atteinte."""


def _prune_old_entries(ip: str) -> None:
    cutoff = time.time() - 3600
    _ip_execution_log[ip] = [t for t in _ip_execution_log[ip] if t > cutoff]


def check_and_record_execution(ip: str) -> None:
    """Une "exécution" = un clic sur "Approuver et exécuter tout" en mode serveur,
    peu importe le nombre de sous-actions qu'il contient."""
    _prune_old_entries(ip)
    if len(_ip_execution_log[ip]) >= MAX_EXECUTIONS_PER_HOUR_PER_IP:
        raise RateLimitExceeded(
            "Limite de démonstration atteinte pour votre adresse IP "
            f"({MAX_EXECUTIONS_PER_HOUR_PER_IP} exécutions par heure). "
            "Réessayez plus tard, ou basculez en mode simulation."
        )
    _ip_execution_log[ip].append(time.time())


def check_and_record_email() -> None:
    """Un "email" = un envoi SMTP réel (email simple ou invitation .ics), compté
    globalement tous visiteurs confondus pour protéger le compte de démo."""
    today = date.today().isoformat()
    if _daily_email_count["date"] != today:
        _daily_email_count["date"] = today
        _daily_email_count["count"] = 0
    if _daily_email_count["count"] >= MAX_EMAILS_PER_DAY:
        raise RateLimitExceeded(
            f"Limite de démonstration atteinte pour aujourd'hui ({MAX_EMAILS_PER_DAY} emails). "
            "Réessayez demain, ou basculez en mode simulation."
        )
    _daily_email_count["count"] += 1

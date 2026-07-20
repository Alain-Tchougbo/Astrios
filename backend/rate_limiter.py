import os
import threading
import time
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

# Compteurs en mémoire (pas persistés en base) : suffisant pour une démo hackathon
# sur un process unique — un redémarrage remet les compteurs à zéro, ce qui est un
# compromis acceptable pour ce contexte.
MAX_EXECUTIONS_PER_HOUR_PER_IP = 5
MAX_EMAILS_PER_DAY = 30

# Même fuseau que le reste de l'app : la journée de quota doit basculer à minuit
# chez l'utilisateur, pas à minuit dans le fuseau du serveur qui héberge la démo
# (une instance ECS à Singapour changeait de jour en pleine après-midi européenne).
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Africa/Porto-Novo")

_ip_execution_log: dict[str, list[float]] = defaultdict(list)
_daily_email_count = {"date": None, "count": 0}

# Les endpoints FastAPI sont synchrones, donc exécutés dans un pool de threads :
# sans verrou, le motif "vérifier puis incrémenter" n'est pas atomique et deux
# requêtes simultanées pouvaient passer le contrôle avant que l'une n'incrémente,
# dépassant la limite. La mutation d'un defaultdict depuis plusieurs threads
# n'était pas sûre non plus.
_lock = threading.Lock()


class RateLimitExceeded(Exception):
    """Levée quand une limite anti-abus du mode serveur (démo publique) est atteinte."""


def _today_str() -> str:
    return datetime.now(ZoneInfo(APP_TIMEZONE)).date().isoformat()


def _prune_old_entries(ip: str) -> None:
    cutoff = time.time() - 3600
    _ip_execution_log[ip] = [t for t in _ip_execution_log[ip] if t > cutoff]


def check_and_record_execution(ip: str) -> None:
    """Une "exécution" = un clic sur "Approuver et exécuter tout" en mode serveur,
    peu importe le nombre de sous-actions qu'il contient."""
    with _lock:
        _prune_old_entries(ip)
        if len(_ip_execution_log[ip]) >= MAX_EXECUTIONS_PER_HOUR_PER_IP:
            raise RateLimitExceeded(
                "Limite de démonstration atteinte pour votre adresse IP "
                f"({MAX_EXECUTIONS_PER_HOUR_PER_IP} exécutions par heure). "
                "Réessayez plus tard, ou basculez en mode simulation."
            )
        _ip_execution_log[ip].append(time.time())


def check_and_record_email() -> None:
    """Réserve un crédit d'envoi AVANT l'envoi réel : compté globalement, tous
    visiteurs confondus, pour protéger le compte de démo. Si l'envoi échoue ensuite,
    l'appelant doit rendre le crédit via refund_email() — voir sa docstring."""
    with _lock:
        today = _today_str()
        if _daily_email_count["date"] != today:
            _daily_email_count["date"] = today
            _daily_email_count["count"] = 0
        if _daily_email_count["count"] >= MAX_EMAILS_PER_DAY:
            raise RateLimitExceeded(
                f"Limite de démonstration atteinte pour aujourd'hui ({MAX_EMAILS_PER_DAY} emails). "
                "Réessayez demain, ou basculez en mode simulation."
            )
        _daily_email_count["count"] += 1


def refund_email() -> None:
    """Rend le crédit réservé quand l'envoi a finalement échoué (SMTP injoignable,
    destinataire refusé...). Sans ça, une panne SMTP passagère consommait
    définitivement le quota du jour alors qu'aucun email n'était jamais parti — la
    démo devenait inutilisable jusqu'au lendemain. Plancher à 0 pour rester correct
    même si la réservation correspondante a été neutralisée (tests)."""
    with _lock:
        if _daily_email_count["date"] == _today_str():
            _daily_email_count["count"] = max(0, _daily_email_count["count"] - 1)

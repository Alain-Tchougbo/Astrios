import os
import urllib.parse
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Fuseau dans lequel les horaires "muraux" issus de mission_facts (ex. "13:00")
# doivent être interprétés — configurable car la démo n'est pas nécessairement
# hébergée/utilisée dans le même fuseau que l'utilisateur. Africa/Porto-Novo est
# UTC+1 toute l'année (pas d'heure d'été), ce qui simplifie le bloc VTIMEZONE
# ci-dessous (un seul décalage fixe, pas de règle de transition à modéliser).
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Africa/Porto-Novo")


def _escape(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _local_datetime(naive_str: str) -> datetime:
    return datetime.strptime(naive_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=ZoneInfo(APP_TIMEZONE))


def _to_ics_wall_datetime(naive_str: str) -> str:
    """naive_str : "YYYY-MM-DDTHH:MM:SS", horaire mural dans APP_TIMEZONE. Formaté tel
    quel (sans conversion) pour être utilisé avec un DTSTART/DTEND;TZID=... — c'est le
    bloc VTIMEZONE associé (voir _build_vtimezone) qui indique au client comment
    interpréter cette heure locale."""
    return naive_str.replace("-", "").replace(":", "")


def _to_utc_datetime(naive_str: str) -> str:
    """Conversion en UTC (forme "...Z"), utilisée pour le lien "Ajouter à Google
    Calendar" (le paramètre "dates" de calendar.google.com/calendar/render attend du
    UTC), pas pour le DTSTART/DTEND du .ics qui utilise TZID (voir ci-dessus)."""
    return _local_datetime(naive_str).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _utc_offset_str(reference_year: int) -> str:
    """Calcule le décalage UTC de APP_TIMEZONE et le formate en "+HHMM"/"-HHMM" pour
    TZOFFSETFROM/TZOFFSETTO. Suppose un décalage FIXE toute l'année (vrai pour
    Africa/Porto-Novo, qui n'observe pas l'heure d'été) : le bloc VTIMEZONE généré
    n'a qu'un seul composant STANDARD sans règle de transition. Un fuseau avec heure
    d'été donnerait un VTIMEZONE incomplet (ce n'est pas le cas d'usage de cette app,
    limite documentée plutôt que gérée par une table de règles complète)."""
    zone = ZoneInfo(APP_TIMEZONE)
    offset = datetime(reference_year, 1, 1, tzinfo=zone).utcoffset()
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"{sign}{hours:02d}{minutes:02d}"


def _build_vtimezone(reference_year: int) -> list[str]:
    offset = _utc_offset_str(reference_year)
    tzname = APP_TIMEZONE.rsplit("/", maxsplit=1)[-1]
    return [
        "BEGIN:VTIMEZONE",
        f"TZID:{APP_TIMEZONE}",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        f"TZOFFSETFROM:{offset}",
        f"TZOFFSETTO:{offset}",
        f"TZNAME:{tzname}",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]


def build_ics_invite(
    titre: str,
    description: str,
    date_debut: str,
    date_fin: str,
    organizer_email: str,
    organizer_name: str,
    attendee_email: str,
) -> str:
    """Génère un fichier iCalendar (VCALENDAR/VEVENT, METHOD:REQUEST) standard,
    utilisé en mode serveur (pas de compte Google connecté) pour envoyer une
    invitation calendrier par email — Gmail/Outlook affichent alors des boutons
    Accepter/Refuser directement dans le message.

    DTSTART/DTEND utilisent TZID=<APP_TIMEZONE> avec un bloc VTIMEZONE embarqué
    (plutôt qu'une conversion UTC "Z") : conforme RFC 5545 même pour un client qui
    ne connaîtrait pas APP_TIMEZONE via sa propre base IANA.
    """
    reference_year = _local_datetime(date_debut).year
    uid = f"{uuid.uuid4()}@astrios-demo"
    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dtstart = _to_ics_wall_datetime(date_debut)
    dtend = _to_ics_wall_datetime(date_fin)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Astrios//Demo Hackathon//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:REQUEST",
        *_build_vtimezone(reference_year),
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART;TZID={APP_TIMEZONE}:{dtstart}",
        f"DTEND;TZID={APP_TIMEZONE}:{dtend}",
        f"SUMMARY:{_escape(titre)}",
        f"DESCRIPTION:{_escape(description)}",
        f"ORGANIZER;CN={_escape(organizer_name)}:mailto:{organizer_email}",
        f"ATTENDEE;CN={_escape(attendee_email)};RSVP=TRUE;PARTSTAT=NEEDS-ACTION:mailto:{attendee_email}",
        "STATUS:CONFIRMED",
        "SEQUENCE:0",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"


def build_google_calendar_link(
    titre: str,
    description: str,
    date_debut: str,
    date_fin: str,
    location: str = "",
) -> str:
    """Construit le lien "Ajouter à Google Calendar" (action=TEMPLATE) affiché comme
    bouton dans le corps HTML de l'email — un mécanisme indépendant de la pièce
    jointe .ics : certains clients mail/webmails n'affichent pas le bandeau natif
    Oui/Peut-être/Non (ou l'utilisateur consulte l'email sur un client qui ignore
    text/calendar), ce bouton reste alors le moyen de secours pour ajouter
    l'événement. Les dates sont en UTC, comme attendu par ce endpoint Google."""
    params = {
        "action": "TEMPLATE",
        "text": titre,
        "dates": f"{_to_utc_datetime(date_debut)}/{_to_utc_datetime(date_fin)}",
        "details": description,
        "location": location,
    }
    return "https://calendar.google.com/calendar/render?" + urllib.parse.urlencode(params)

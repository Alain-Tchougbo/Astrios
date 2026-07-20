import os
import urllib.parse
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Fuseau dans lequel les horaires "muraux" issus de mission_facts (ex. "13:00")
# doivent être interprétés — configurable car la démo n'est pas nécessairement
# hébergée/utilisée dans le même fuseau que l'utilisateur. Africa/Porto-Novo est
# UTC+1 toute l'année (pas d'heure d'été), ce qui simplifie le bloc VTIMEZONE
# ci-dessous (un seul décalage fixe, pas de règle de transition à modéliser).
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Africa/Porto-Novo")


class IcsBuildError(Exception):
    """Levée quand une date/heure d'action n'est pas exploitable pour construire
    l'invitation. Sans ça, un format inattendu (secondes manquantes, valeur None,
    suffixe de fuseau) faisait remonter un ValueError/TypeError brut depuis
    strptime, présenté à l'utilisateur comme « une erreur inattendue » sans lui
    dire quoi corriger. Traitée comme une erreur connue par le routeur (voir
    _SERVER_MODE_ERRORS) pour que son message soit affiché tel quel."""


# RFC 5545 §3.1 : une ligne de contenu ne doit pas dépasser 75 octets (hors CRLF).
_MAX_LINE_OCTETS = 75


def _escape(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold_line(line: str) -> str:
    """Plie une ligne trop longue selon RFC 5545 §3.1 : découpe en morceaux de 75
    octets maximum, les suivants précédés d'une espace (qui compte dans la limite,
    d'où 74 octets utiles). Le découpage se fait sur des frontières de CARACTÈRES,
    jamais au milieu d'une séquence UTF-8 multi-octets — un SUMMARY/DESCRIPTION
    accentué dépasse 75 octets bien avant 75 caractères. Sans pliage, les parseurs
    stricts (certaines piles Outlook/Exchange) rejettent ou tronquent l'invitation ;
    Gmail est tolérant, ce qui masquait le problème en démo."""
    if len(line.encode("utf-8")) <= _MAX_LINE_OCTETS:
        return line

    chunks: list[str] = []
    current = ""
    current_octets = 0
    limit = _MAX_LINE_OCTETS
    for char in line:
        char_octets = len(char.encode("utf-8"))
        if current_octets + char_octets > limit:
            chunks.append(current)
            current = ""
            current_octets = 0
            # Les lignes de continuation commencent par une espace, comptée dans les 75.
            limit = _MAX_LINE_OCTETS - 1
        current += char
        current_octets += char_octets
    chunks.append(current)
    return "\r\n ".join(chunks)


def _local_datetime(naive_str: str) -> datetime:
    try:
        zone = ZoneInfo(APP_TIMEZONE)
    except ZoneInfoNotFoundError as exc:
        # Distinct d'une date invalide : ici c'est la CONFIGURATION du serveur qui est
        # en cause (APP_TIMEZONE inconnu, ou paquet tzdata absent de l'environnement),
        # pas la saisie de l'utilisateur — qui n'a donc rien à corriger de son côté.
        raise IcsBuildError(
            f"Fuseau horaire APP_TIMEZONE invalide ou introuvable ({APP_TIMEZONE!r}) — "
            "vérifie cette variable d'environnement et la présence du paquet tzdata."
        ) from exc

    try:
        return datetime.strptime(naive_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=zone)
    except (ValueError, TypeError) as exc:
        raise IcsBuildError(
            f"Date ou heure d'événement invalide ({naive_str!r}) — format attendu : "
            "AAAA-MM-JJTHH:MM:SS. Modifie l'action pour corriger la date avant d'exécuter."
        ) from exc


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
    uid: str | None = None,
) -> str:
    """Génère un fichier iCalendar (VCALENDAR/VEVENT, METHOD:REQUEST) standard,
    utilisé en mode serveur (pas de compte Google connecté) pour envoyer une
    invitation calendrier par email — Gmail/Outlook affichent alors des boutons
    Accepter/Refuser directement dans le message.

    DTSTART/DTEND utilisent TZID=<APP_TIMEZONE> avec un bloc VTIMEZONE embarqué
    (plutôt qu'une conversion UTC "Z") : conforme RFC 5545 même pour un client qui
    ne connaîtrait pas APP_TIMEZONE via sa propre base IANA.

    uid : identifiant iCalendar de l'événement. Un client mail identifie un
    événement par son UID — deux invitations de UID différents créent DEUX
    événements distincts dans l'agenda du destinataire, même si tout le reste est
    identique. Il faut donc passer un UID STABLE, dérivé de l'action persistée
    (voir _action_ics_uid dans routers/missions.py) : sans ça, un renvoi de la même
    invitation (retry après un échec partiel, ou une action rééditée puis
    réexécutée) produisait un DOUBLON au lieu d'une mise à jour. Par défaut, un UID
    aléatoire est généré — repli acceptable uniquement pour un envoi ponctuel sans
    action associée.
    """
    reference_year = _local_datetime(date_debut).year
    # Validé même si la valeur formatée ne passe pas par _local_datetime : une
    # date de fin malformée doit échouer ici avec un message clair, pas produire
    # un DTEND silencieusement invalide dans l'invitation envoyée.
    _local_datetime(date_fin)
    uid = uid or f"{uuid.uuid4()}@astrios-demo"
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
    return "\r\n".join(_fold_line(line) for line in lines) + "\r\n"


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

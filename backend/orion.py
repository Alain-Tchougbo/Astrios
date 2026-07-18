import json
import os
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.getenv("QWEN_API_KEY"),
    base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
)

MODEL = "qwen3.7-plus"
RETRY_DELAY_SECONDS = 2
# Même fuseau que ics_builder.py/google_calendar.py : "aujourd'hui" et les dates
# relatives ("demain", "samedi prochain") doivent être résolues dans le fuseau de
# l'utilisateur, pas celui du serveur qui héberge l'API.
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Africa/Porto-Novo")


def _today_str() -> str:
    return datetime.now(ZoneInfo(APP_TIMEZONE)).strftime("%Y-%m-%d")

class OrionAPIError(Exception):
    """Levée quand un appel à Qwen échoue après la tentative de retry."""


def _create_completion(messages: list[dict], json_mode: bool = False):
    kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            return client.chat.completions.create(model=MODEL, messages=messages, **kwargs)
        except Exception as exc:  # noqa: BLE001 - on relaie l'erreur d'origine après retry
            last_error = exc
            if attempt == 0:
                time.sleep(RETRY_DELAY_SECONDS)
    raise last_error


SYSTEM_PROMPT = """Tu es Orion, le Chief of Staff IA d'Astrios. Tu accompagnes l'utilisateur dans le cadrage et l'exécution de ses missions professionnelles.

PERSONNALITÉ :
- Direct, professionnel, précis. Jamais familier, jamais bavard.
- Tu ne dis jamais "Bien sûr !", "Avec plaisir", "Super !" ou toute formule enthousiaste. Préfère des formulations sobres : "Compris.", "Entendu.", "Noté."
- Phrases courtes, qui vont à l'essentiel. Pas d'emoji.
- Vocabulaire simple et concret, jamais de jargon. Un non-spécialiste doit comprendre chaque mot du premier coup.
  Exemples de mot à ne JAMAIS utiliser -> formulation correcte à la place :
  - "périmètre fonctionnel" -> "quelles fonctionnalités précises"
  - "livrables" -> "ce que vous devez rendre/produire à la fin"
  - "parties prenantes" -> "les personnes concernées par ce projet"
  Avant d'envoyer ta question, relis-la : si elle contient un mot qu'un non-spécialiste ne comprendrait pas immédiatement, reformule-la en langage courant.

FORMAT DE RÉPONSE — phase de découverte :
Tu dois TOUJOURS répondre avec un objet JSON strictement valide, de cette forme exacte :
{"message": "...", "suggestions": ["...", "..."], "discovery_complete": false}

- "message" : le texte que l'utilisateur va lire (ta question, ta reformulation, ou ta synthèse finale). Applique-lui toutes les règles de ce prompt.
- "suggestions" : une liste de 0 à 4 réponses rapides que l'utilisateur peut cliquer au lieu de taper sa réponse. N'en fournis QUE quand la question se prête à des réponses fermées ou catégorisables (ex. type de contrat, tranche de budget, niveau d'urgence, oui/non). Laisse une liste vide ([]) pour une question ouverte qui appelle une réponse libre et détaillée (ex. "Décris le projet en quelques phrases."). Chaque suggestion doit être courte (quelques mots), formulée comme une réponse que l'utilisateur donnerait telle quelle (jamais une question).
- "discovery_complete" : true UNIQUEMENT quand la découverte est terminée (voir règle 4) et que "message" est une synthèse sans aucune question. false dans tous les autres cas.
- Aucun texte hors du JSON. Pas de markdown autour du JSON, pas de blocs ```json, pas de commentaire.

MÉTHODE DE TRAVAIL — phase de découverte :
1. Comprends l'objectif général de la mission à partir des messages de l'utilisateur.
2. Pose entre 3 et 5 questions de clarification pour cadrer précisément la mission (contexte, contraintes, critères de succès, délais, budget, ressources disponibles — adapte les questions à la mission concernée).
3. RÈGLE ABSOLUE : une seule information demandée par message. Jamais deux. Ceci s'applique même si les deux informations semblent liées ou mineures.
   - INTERDIT : combiner deux demandes dans la même phrase, y compris via "et", "ou", une virgule, ou deux phrases dans le même message.
     Exemple à ne JAMAIS faire : "Quel est le budget prévu et le type de contrat souhaité (freelance ou CDI) ?"
   - CORRECT : ne poser qu'une seule de ces deux questions, puis attendre la réponse avant de poser l'autre.
     Exemple correct : "Quel est le budget prévu pour cette mission ?" (et rien d'autre dans "message")
   - Avant d'envoyer ta réponse, vérifie-la : si "message" contient plus d'un point d'interrogation, ou demande plus d'une information distincte, reformule pour n'en garder qu'une et retire le reste — tu le demanderas au tour suivant.
   - Attends toujours la réponse de l'utilisateur avant de poser la question suivante.
4. Dès que tu as assez d'informations pour construire un plan d'action (généralement après 3 à 5 échanges), mets "discovery_complete" à true et écris dans "message" un court texte de synthèse qui récapitule TOUTES les informations déjà collectées, sans aucune question.
   RÈGLE ABSOLUE : "discovery_complete" ne doit JAMAIS être true si "message" contient un point d'interrogation ou pose une nouvelle question, même implicitement.
     Exemple correct : {"message": "Compris. Je récapitule : webinaire de lancement de la fonctionnalité d'export PDF, dans 5 semaines, avec toi et le responsable produit comme intervenants, ciblant les clients existants et les prospects chauds, promotion par email marketing, budget de 800 €. J'ai ce qu'il me faut pour construire le plan.", "suggestions": [], "discovery_complete": true}
   - Avant d'envoyer ta réponse, vérifie-la une dernière fois : si "message" contient un "?", "discovery_complete" doit rester false — même si tu penses avoir assez d'informations. Pose d'abord cette dernière question, attends la réponse de l'utilisateur, puis écris la synthèse dans un message séparé.

RÈGLE ABSOLUE — zéro champ à remplir plus tard (pas de placeholder) :
CHECKLIST OBLIGATOIRE, à exécuter EXPLICITEMENT et dans l'ordre avant CHAQUE mise à true de "discovery_complete", sur la conversation entière — jamais sautée, même si tu penses avoir assez d'informations pour un plan, même si l'utilisateur te demande explicitement de conclure sans attendre :
1. Un email va-t-il probablement être nécessaire pour cette mission ? Vérifie précisément : les mots "mail", "email", "e-mail", "courriel", "contacter", "écrire à", "envoyer à", "prévenir", "relancer" apparaissent-ils dans un message de L'UTILISATEUR (pas seulement dans tes propres questions) ? Si OUI : l'adresse email réelle du ou des destinataires a-t-elle été donnée explicitement, en toutes lettres, dans la conversation ? Si NON, tu DOIS la demander comme question de découverte avant de conclure — ne conclus JAMAIS en sachant qu'il faudra deviner, improviser ou utiliser une adresse de test. Si l'utilisateur insiste pour conclure sans donner l'adresse (ex. "utilise une adresse de test", "conclus quand même", "on verra plus tard"), refuse poliment mais fermement : explique que tu as besoin de la vraie adresse et laisse "discovery_complete" à false.
   EXCEPTION IMPORTANTE : si le SEUL indice d'un besoin d'email est qu'une réunion/rendez-vous est planifié avec une personne nommée, mais que L'UTILISATEUR n'a lui-même jamais demandé d'envoyer un email ou une invitation, ne demande PAS l'adresse directement ici — ce n'est pas un besoin exprimé, seulement une possibilité. Applique à la place la RÈGLE "anticipation propre par question" ci-dessous, qui commence par une question oui/non avant de demander une adresse.
2. Existe-t-il une autre information factuelle indispensable mentionnée mais jamais précisée ? Vérifie systématiquement : le nom exact d'une personne ou d'une entreprise à qui s'adresser, le nom exact d'une entité/produit/projet concerné, une date ou un montant évoqués mais jamais chiffrés, ou tout autre champ concret qu'un document ou une action devra réutiliser tel quel. Si OUI pour l'un de ces points, demande-le aussi avant de conclure.
Ne mets JAMAIS "discovery_complete" à true en sachant qu'un champ concret (adresse email, nom de destinataire, nom d'entité...) restera à deviner ou à remplir plus tard par l'utilisateur.
  Exemple à ne JAMAIS faire (cas réellement observé) : une mission de recrutement où l'utilisateur écrit "il faut envoyer un mail à Julien pour lui proposer le poste". Le NOM a été donné ("Julien"), mais PAS son adresse email — tu ne dois PAS conclure la découverte ici. Conclure produirait une action email avec un destinataire de test ou un placeholder, alors qu'une vraie personne à contacter a été identifiée par son nom.
  Exemple correct dans ce même cas : {"message": "Quelle est l'adresse email de Julien, pour lui envoyer la proposition ?", "suggestions": [], "discovery_complete": false}
  Exemple à ne JAMAIS faire (autre cas) : conclure la découverte d'un lancement de bêta sans avoir demandé le nom de l'application ni l'adresse email des testeurs à contacter — les documents et l'email générés ensuite contiendraient alors des placeholders comme "[Nom de l'appli]" ou "[email dédié]".

RÈGLE — anticipation propre par question (jamais par action incomplète) :
Si la mission implique un rendez-vous ou une réunion avec une personne NOMMÉE, mais que l'utilisateur n'a lui-même JAMAIS mentionné vouloir envoyer un email ou une invitation à cette personne, tu PEUX (une seule fois, avant de conclure) poser UNE question proactive de cette forme exacte : "Souhaitez-vous que j'envoie aussi une invitation à {nom} ? Si oui, quelle est son adresse email ?" (remplace {nom} par le nom réel). Cette question compte comme une seule question malgré ses deux volets (oui/non, puis adresse si oui) — elle ne viole pas la règle "une seule information par message".
- Ne pose PAS cette question si l'utilisateur a déjà lui-même mentionné explicitement vouloir (ou ne pas vouloir) envoyer un email/une invitation pour cette réunion : dans ce cas, suis simplement la règle du dessus (demande l'adresse s'il en manque une, ou n'en parle pas s'il n'a rien demandé).
- Si l'utilisateur répond par la négative, ou n'importe quoi d'autre qu'une adresse email ou un "oui", n'insiste JAMAIS et ne redemande plus : conclus la découverte sans email pour cette personne, seul l'événement calendrier sera créé.
- Si l'utilisateur répond "oui" sans donner d'adresse dans le même message, demande l'adresse dans une question de suivi avant de conclure.
- Si l'utilisateur donne directement une adresse email, poursuis normalement (vérifie ensuite s'il faut aussi demander le nom de l'expéditeur, comme pour tout email).
RAISON D'ÊTRE de cette règle : ne JAMAIS créer d'action email avec une adresse inventée ou un destinataire de test — soit une vraie adresse a été explicitement acceptée par l'utilisateur, soit aucune action email n'est créée du tout.

RÈGLE ABSOLUE — réponses non-exploitables de l'utilisateur :
Si la réponse de l'utilisateur ne contient AUCUNE information exploitable pour la mission (ex. "ok", "comment tu vas ?", un simple accusé de réception, une question hors-sujet, un mot isolé sans rapport), tu NE DOIS JAMAIS reposer la question précédente à l'identique, mot pour mot. C'est un bug grave si ça se produit.
- 1ère fois que ça arrive sur une question donnée : réponds brièvement et professionnellement à la remarque hors-sujet si nécessaire, puis reformule la question DIFFÉREMMENT, avec un exemple concret pour aider l'utilisateur à comprendre ce qui est attendu.
  Exemple : tu as posé "Quel est le budget prévu ?" et l'utilisateur répond "ok" (aucune information exploitable). Tu NE DOIS PAS reposer "Quel est le budget prévu ?" à l'identique. Réponds plutôt :
  {"message": "Je n'ai pas capté de montant précis — avez-vous une fourchette en tête, par exemple entre 1000€ et 5000€, ou est-ce encore à définir ?", "suggestions": ["Entre 1000€ et 5000€", "Plus de 5000€", "Pas encore défini"], "discovery_complete": false}
- 2e fois DE SUITE que la réponse à cette même question n'est toujours pas exploitable : propose explicitement 2 à 3 exemples de réponses possibles dans "suggestions" pour débloquer la situation, et mentionne-les aussi dans "message".

RÈGLE ABSOLUE — changement de sujet :
Si le nouveau message de l'utilisateur semble complètement déconnecté de l'objectif initial de la mission, ne continue pas comme si de rien n'était : signale EXPLICITEMENT cette rupture dans "message" avant toute chose, et laisse "discovery_complete" à false.
  Exemple : la mission portait sur "mettre à jour la comptabilité du mois" et l'utilisateur écrit soudain "génère-moi une vidéo publicitaire Coca-Cola". Réponds :
  {"message": "Je remarque que votre demande initiale portait sur la mise à jour de la comptabilité du mois, et vous mentionnez maintenant une vidéo publicitaire Coca-Cola — souhaitez-vous que je réoriente entièrement cette mission vers ce nouveau sujet, ou est-ce un complément à la demande initiale ?", "suggestions": ["Réorienter entièrement la mission vers ce nouveau sujet", "C'est un complément, pas un changement", "Non, ignore cette remarque, reprenons où on en était"], "discovery_complete": false}

Tu es strictement en phase de découverte : tu ne proposes pas encore de plan, tu ne fais que qualifier la mission."""


def _ask_orion_raw(history: list[dict]) -> dict:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history]
    try:
        response = _create_completion(messages, json_mode=True)
    except Exception:
        try:
            response = _create_completion(messages, json_mode=False)
        except Exception as exc:
            raise OrionAPIError(
                "Orion n'a pas pu répondre après deux tentatives. Réessaie dans un instant."
            ) from exc

    raw = response.choices[0].message.content

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(match.group(0)) if match else None

    if not isinstance(data, dict) or not (data.get("message") or "").strip():
        # Repli si Qwen n'a pas respecté le format JSON demandé : le texte brut devient
        # le message, sans suggestions, découverte considérée non terminée par prudence.
        return {"message": raw.strip(), "suggestions": [], "discovery_complete": False}

    suggestions_raw = data.get("suggestions")
    suggestions = (
        [s.strip() for s in suggestions_raw if isinstance(s, str) and s.strip()][:4]
        if isinstance(suggestions_raw, list)
        else []
    )

    return {
        "message": data["message"].strip(),
        "suggestions": suggestions,
        "discovery_complete": bool(data.get("discovery_complete")),
    }


# Mots-clés indiquant une intention d'email quelque part dans la conversation.
EMAIL_INTENT_KEYWORDS = [
    "mail",
    "email",
    "e-mail",
    "courriel",
    "écrire à",
    "ecrire a",
    "contacter",
]
_EMAIL_REGEX = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

_EMAIL_CORRECTION_NUDGE = (
    "[Consigne système : la conversation mentionne l'envoi d'un email/mail mais aucune "
    "adresse email valide n'a encore été donnée. Ne conclus PAS la découverte maintenant. "
    "Pose une question dédiée pour obtenir cette adresse.]"
)

_EMAIL_FALLBACK_QUESTION = (
    "Avant de continuer, il me manque une information : quelle est l'adresse email du "
    "destinataire mentionné dans cette conversation ?"
)


def has_email_intent(history: list[dict]) -> bool:
    """Détection déterministe (mots-clés) : L'UTILISATEUR a-t-il lui-même évoqué l'envoi
    d'un email, peu importe si une adresse ou un nom d'expéditeur ont déjà été donnés ?
    Ne scanne QUE les messages "user", jamais les messages "assistant" : la question
    proactive d'anticipation posée par Orion lui-même (voir SYSTEM_PROMPT, "Souhaitez-vous
    que j'envoie aussi une invitation...") mentionne le mot "email" — si on scannait aussi
    les messages assistant, un simple "non" de l'utilisateur en réponse à cette question ne
    suffirait pas à désactiver ce signal, et forcerait à tort une question de relance sur
    l'adresse/l'expéditeur alors que l'utilisateur vient justement de refuser l'email."""
    full_text = " ".join(m.get("content", "") for m in history if m.get("role") == "user").lower()
    return any(kw in full_text for kw in EMAIL_INTENT_KEYWORDS)


def _conversation_needs_email(history: list[dict]) -> bool:
    """Vérification déterministe (regex), indépendante du prompt. Le prompt seul s'est
    révélé insuffisant en pratique pour garantir qu'Orion demande toujours l'adresse
    email avant de conclure la découverte (bug observé en test réel : une mission
    mentionnant "envoyer un mail à quelqu'un" a été conclue sans jamais demander son
    adresse). Une regex sur les messages est déterministe, donc fiable à 100%."""
    full_text = " ".join(m.get("content", "") for m in history).lower()
    has_email = bool(_EMAIL_REGEX.search(full_text))
    return has_email_intent(history) and not has_email


def ask_orion(history: list[dict]) -> dict:
    reply = _ask_orion_raw(history)

    if reply["discovery_complete"] and _conversation_needs_email(history):
        # 1ère ligne de défense : on redonne une chance à Orion, avec une consigne
        # explicite injectée pour ce seul tour (jamais persistée en base), de poser la
        # question au lieu de conclure.
        corrected_history = history + [{"role": "user", "content": _EMAIL_CORRECTION_NUDGE}]
        reply = _ask_orion_raw(corrected_history)

        if reply["discovery_complete"] and _conversation_needs_email(history):
            # Filet de sécurité ultime : même si Qwen ignore la consigne deux fois de
            # suite, le check étant déterministe, on n'accepte JAMAIS discovery_complete
            # dans ce cas — on force la question nous-mêmes plutôt que de laisser passer
            # une action email sans destinataire réel.
            reply = {
                "message": reply["message"] if "?" in reply["message"] else _EMAIL_FALLBACK_QUESTION,
                "suggestions": reply["suggestions"] or [],
                "discovery_complete": False,
            }

    return reply


MISSION_FACTS_SYSTEM_PROMPT_TEMPLATE = """Tu es Orion, Chief of Staff IA. Ta seule tâche ici : extraire de la conversation de découverte d'une mission TOUS les faits concrets et vérifiables qui devront ensuite rester rigoureusement cohérents entre le plan, les documents et les actions générés séparément pour cette mission.

Réponds UNIQUEMENT avec un objet JSON strictement valide, de cette forme exacte :
{{"destinataires": [{{"nom": "...", "email": "..." ou null}}], "rendez_vous": [{{"objet": "...", "date": "YYYY-MM-DD" ou null, "heure": "HH:MM" ou null, "duree_minutes": <nombre entier> ou null}}], "entites": ["..."], "delais": ["..."], "contraintes": ["..."], "sender_name": "..." ou null}}

Date de référence (aujourd'hui) : {reference_date}

RÈGLE ABSOLUE : n'invente RIEN. Si une information n'a pas été donnée explicitement dans la conversation, laisse le champ à null (ou omets l'entrée dans une liste).
- "date" d'un rendez-vous : si une date relative mais précise est mentionnée (ex. "samedi prochain", "dans 3 jours"), calcule la date absolue (YYYY-MM-DD) à partir de la date de référence ci-dessus — ce calcul est autorisé, ce n'est pas une invention, et c'est justement ce qui garantit que tous les livrables utilisent la MÊME date au lieu de la recalculer chacun de leur côté. Si la date reste vague (ex. "bientôt", "la semaine prochaine" sans jour précis), laisse "date" à null.
- "rendez_vous" : un élément par rendez-vous/appel/entretien/événement distinct mentionné. "duree_minutes" est un nombre entier de minutes (ex. 30, 60), jamais une chaîne de texte.
- "destinataires" : les personnes ou entités à qui une communication est destinée. "email" reste null si aucune adresse n'a été donnée.
- "entites" : noms exacts de personnes, entreprises, applications ou produits mentionnés, à réutiliser tels quels partout.
- "delais" : échéances mentionnées, reprises telles quelles (ex. "dans 3 semaines").
- "contraintes" : toute autre contrainte factuelle (budget, format, lieu, etc.).
- "sender_name" : le nom ou prénom de la personne au nom de qui l'email doit être signé (l'utilisateur lui-même), UNIQUEMENT s'il a été donné explicitement (ex. "je m'appelle...", "signe de la part de...", "mon nom est..."). Reste null si ça n'a jamais été précisé — ne déduis JAMAIS un nom à partir du contexte.
- Aucun texte hors du JSON. Pas de markdown autour du JSON, pas de blocs ```json, pas de commentaire."""


def extract_mission_facts(mission_objectif: str | None, history: list[dict]) -> dict:
    """Extrait une seule fois, à la fin de la découverte, les faits factuels de la
    conversation — partagés ensuite avec generate_plan/generate_documents/
    propose_action pour qu'ils ne divergent plus entre eux (voir _format_mission_facts
    et le bug observé : un document annonçant "30 minutes" pendant qu'une action
    calendrier était créée avec 60 minutes)."""
    conversation_text = "\n".join(f"{m.get('role', '?')}: {m.get('content', '')}" for m in history)
    user_prompt = (
        f"Objectif de la mission : {mission_objectif or 'non précisé'}\n\n"
        f"Conversation de découverte :\n{conversation_text}\n\n"
        "Extrais les faits au format JSON demandé."
    )
    system_prompt = MISSION_FACTS_SYSTEM_PROMPT_TEMPLATE.format(reference_date=_today_str())
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    try:
        response = _create_completion(messages, json_mode=True)
    except Exception:
        try:
            response = _create_completion(messages, json_mode=False)
        except Exception as exc:
            raise OrionAPIError(
                "L'extraction des faits de la mission a échoué après deux tentatives. Réessaie dans un instant."
            ) from exc

    raw = response.choices[0].message.content
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(match.group(0)) if match else {}

    if not isinstance(data, dict):
        data = {}

    raw_sender_name = data.get("sender_name")
    sender_name = raw_sender_name.strip() if isinstance(raw_sender_name, str) and raw_sender_name.strip() else None

    return {
        "destinataires": data.get("destinataires") if isinstance(data.get("destinataires"), list) else [],
        "rendez_vous": data.get("rendez_vous") if isinstance(data.get("rendez_vous"), list) else [],
        "entites": data.get("entites") if isinstance(data.get("entites"), list) else [],
        "delais": data.get("delais") if isinstance(data.get("delais"), list) else [],
        "contraintes": data.get("contraintes") if isinstance(data.get("contraintes"), list) else [],
        "sender_name": sender_name,
    }


_FRENCH_WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def _french_weekday(date_str: str | None) -> str | None:
    """Calcule le jour de la semaine (locale fr) d'une date "YYYY-MM-DD" côté
    backend — un LLM calcule ce genre de chose de façon peu fiable à partir d'une
    date brute (bug observé : "vendredi 18 juillet 2026" pour un samedi). Retourne
    None si la date est absente/invalide, pour laisser la formulation "à confirmer"
    inchangée dans ce cas."""
    if not date_str:
        return None
    try:
        return _FRENCH_WEEKDAYS[datetime.strptime(date_str, "%Y-%m-%d").weekday()]
    except (ValueError, TypeError):
        return None


def _format_mission_facts(mission_facts: dict | None) -> str:
    """Sérialise mission_facts en un bloc de prompt lisible, injecté dans
    generate_plan/generate_documents/propose_action. Retourne "" si mission_facts
    est absent ou entièrement vide (missions antérieures à cette fonctionnalité)."""
    if not mission_facts or not any(mission_facts.values()):
        return ""

    lines = [
        "FAITS ÉTABLIS PENDANT LA DÉCOUVERTE (source de vérité — à respecter strictement : "
        "ne jamais contredire ces valeurs ni en inventer d'autres à la place) :"
    ]
    for d in mission_facts.get("destinataires") or []:
        nom = d.get("nom") or "non précisé"
        email = d.get("email") or "non donné, à confirmer"
        lines.append(f"- Destinataire : {nom} — email : {email}")
    for r in mission_facts.get("rendez_vous") or []:
        date = r.get("date") or "date à confirmer"
        heure = r.get("heure") or "heure à confirmer"
        duree = f"{r.get('duree_minutes')} minutes" if r.get("duree_minutes") else "durée à confirmer"
        weekday = _french_weekday(r.get("date"))
        if weekday:
            lines.append(
                f"- Rendez-vous : {r.get('objet') or 'non précisé'} — {weekday} {date} à {heure}, "
                f"durée : {duree}. Jour de la semaine déjà calculé côté serveur : {weekday}. Si tu "
                f"mentionnes cette date en toutes lettres dans un texte, utilise EXACTEMENT "
                f"\"{weekday} {date}\" — ne recalcule JAMAIS le jour de la semaine toi-même à "
                "partir de la date : c'est une opération que les LLM effectuent souvent mal."
            )
        else:
            lines.append(
                f"- Rendez-vous : {r.get('objet') or 'non précisé'} — {date} à {heure}, durée : {duree}"
            )
    entites = mission_facts.get("entites") or []
    if entites:
        lines.append(f"- Noms exacts à réutiliser tels quels : {', '.join(entites)}")
    delais = mission_facts.get("delais") or []
    if delais:
        lines.append(f"- Délais : {', '.join(delais)}")
    contraintes = mission_facts.get("contraintes") or []
    if contraintes:
        lines.append(f"- Contraintes : {', '.join(contraintes)}")
    sender_name = mission_facts.get("sender_name")
    if sender_name:
        lines.append(
            f"- Nom de l'expéditeur pour signer tout email : {sender_name} — utilise ce nom en "
            "signature, jamais un placeholder entre crochets comme \"[Votre prénom]\"."
        )
    lines.append(
        "Pour toute valeur marquée \"à confirmer\" ci-dessus, reprends cette formulation ouverte de "
        "façon cohérente partout (plan, documents, actions) plutôt que d'inventer une valeur précise."
    )
    return "\n".join(lines)


DATE_RULE = """- RÈGLE ABSOLUE sur les dates : n'invente JAMAIS une date calendaire précise (jour, mois, année) qui n'a pas été donnée explicitement par l'utilisateur dans la synthèse fournie. Si seule une échéance relative a été mentionnée ("la semaine prochaine", "dans 6 semaines", "fin septembre"), reprends-la telle quelle ou écris "date à confirmer" — ne la convertis jamais en date absolue de ton invention.
  Exemple à ne JAMAIS faire : la synthèse mentionne "démarrage la semaine prochaine" et tu écris "Démarrage : lundi 2 septembre 2024" (date inventée).
  Exemple correct : "Démarrage : semaine prochaine (date exacte à confirmer)."
  Seules les dates explicitement fournies par l'utilisateur (ex. "avant le 27 septembre", "le 4 octobre") peuvent apparaître telles quelles."""


PLAN_SYSTEM_PROMPT = f"""Tu es Orion, Chief of Staff IA. Tu convertis la synthèse d'une mission qualifiée en un plan d'action structuré.

Réponds UNIQUEMENT avec un objet JSON strictement valide, de cette forme exacte :
{{"tasks": [{{"titre": "...", "description": "..."}}, ...]}}

Règles :
- Entre 4 et 8 tâches.
- Les tâches sont ordonnées dans un ordre d'exécution logique : la première de la liste est la première à réaliser.
- "titre" : court et actionnable, commence par un verbe à l'infinitif (ex. "Rédiger l'offre de mission").
- "description" : une phrase courte précisant le livrable ou le critère de complétion de la tâche.
{DATE_RULE}
- Si un bloc "FAITS ÉTABLIS PENDANT LA DÉCOUVERTE" est fourni dans le message utilisateur, ces valeurs (dates, durées, noms, emails) priment sur toute autre déduction : utilise-les exactement telles quelles, ne les contredis jamais.
- Aucun texte hors du JSON. Pas de markdown, pas de blocs ```json, pas de commentaire."""


PLAN_INCREMENTAL_SYSTEM_PROMPT = f"""Tu es Orion, Chief of Staff IA. Cette mission a déjà un plan d'action généré précédemment ; un NOUVEAU besoin vient d'apparaître dans la conversation, après cette première génération (ex. la mission portait sur une réunion, et l'utilisateur demande ensuite d'envoyer aussi un email).

Ta seule tâche : déterminer si ce nouveau besoin nécessite d'AJOUTER une ou deux tâches au plan existant — jamais refaire le plan en entier, jamais redonner une tâche déjà présente.

Réponds UNIQUEMENT avec un objet JSON strictement valide, de cette forme exacte :
{{"tasks": [{{"titre": "...", "description": "..."}}, ...]}}

Règles :
- 0, 1 ou 2 nouvelles tâches, jamais plus.
- Si les tâches déjà existantes couvrent déjà tout ce qui est demandé dans la conversation (rien de réellement nouveau), renvoie une liste VIDE ({{"tasks": []}}) — c'est le comportement attendu pour une simple redite ou une conversation qui n'introduit aucun besoin supplémentaire.
- Ne propose JAMAIS une tâche dont l'objet recouvre une tâche déjà existante (voir liste fournie), même reformulée différemment.
- "titre" : court et actionnable, commence par un verbe à l'infinitif.
- "description" : une phrase courte précisant le livrable ou le critère de complétion de la tâche.
{DATE_RULE}
- Si un bloc "FAITS ÉTABLIS PENDANT LA DÉCOUVERTE" est fourni dans le message utilisateur, ces valeurs priment sur toute autre déduction.
- Aucun texte hors du JSON. Pas de markdown, pas de blocs ```json, pas de commentaire."""


def generate_plan(
    mission_objectif: str | None,
    conversation_summary: str,
    mission_facts: dict | None = None,
    existing_tasks: list[dict] | None = None,
) -> list[dict]:
    """existing_tasks (liste de {"titre", "description"}) : si fourni et non vide, bascule
    en mode incrémental — le plan a déjà été généré pour cette mission, et un nouveau
    besoin est apparu dans la conversation depuis. Renvoie alors UNIQUEMENT les tâches à
    ajouter (0 à 2), jamais un plan complet refait (voir bug : une mission déjà générée
    ne pouvait plus recevoir de nouveau besoin, la génération redémarrait à zéro ou était
    bloquée par un garde-fou anti-doublon)."""
    is_incremental = bool(existing_tasks)
    facts_block = _format_mission_facts(mission_facts)
    existing_block = ""
    if is_incremental:
        existing_list = "\n".join(f"- {t['titre']}" for t in existing_tasks)
        existing_block = f"Tâches déjà existantes dans le plan (ne pas dupliquer) :\n{existing_list}\n\n"

    user_prompt = (
        f"Objectif de la mission : {mission_objectif or 'non précisé'}\n\n"
        f"Synthèse de la phase de découverte :\n{conversation_summary}\n\n"
        + existing_block
        + (f"{facts_block}\n\n" if facts_block else "")
        + (
            "Détermine si de nouvelles tâches doivent être ajoutées au plan existant, au format JSON demandé."
            if is_incremental
            else "Génère le plan d'action au format JSON demandé."
        )
    )
    messages = [
        {"role": "system", "content": PLAN_INCREMENTAL_SYSTEM_PROMPT if is_incremental else PLAN_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    try:
        response = _create_completion(messages, json_mode=True)
    except Exception:
        try:
            response = _create_completion(messages, json_mode=False)
        except Exception as exc:
            raise OrionAPIError(
                "La génération du plan a échoué après deux tentatives. Réessaie dans un instant."
            ) from exc

    raw = response.choices[0].message.content

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(match.group(0)) if match else {"tasks": []}

    tasks = data.get("tasks", [])
    return [
        {"titre": t["titre"].strip(), "description": (t.get("description") or "").strip()}
        for t in tasks
        if isinstance(t, dict) and t.get("titre")
    ]


DOCUMENTS_SYSTEM_PROMPT = f"""Tu es Orion, Chief of Staff IA. Tu rédiges les documents de travail concrets nécessaires pour démarrer l'exécution d'une mission déjà cadrée et planifiée.

Réponds UNIQUEMENT avec un objet JSON strictement valide, de cette forme exacte :
{{"documents": [{{"titre": "...", "type": "...", "purpose": "...", "is_email_action_source": true ou false, "contenu": "..."}}, ...]}}

Règles :
- Produis entre 2 et 3 documents, jamais plus.
- C'est à TOI de déterminer quels documents ont le plus de valeur pour CETTE mission précise, à partir de son objectif, de la synthèse de découverte et du plan d'action fournis. N'applique aucun gabarit fixe : les documents pertinents pour un recrutement (ex. fiche de poste, annonce) ne sont pas ceux d'une campagne marketing (ex. brief créatif, plan média) ni d'un déménagement (ex. checklist logistique, communication aux équipes). Déduis-les du contexte réel de la mission, pas d'un modèle générique.
- "titre" : court et concret (ex. "Fiche de poste — Développeur Flutter").
- "type" : une étiquette courte en un mot, en minuscules, décrivant la nature du document (ex. "fiche", "communication", "brief", "checklist", "plan", "annonce" — choisis le mot le plus juste pour ce document, n'invente pas de catégorie exotique).
- "purpose" : UNE phrase courte et concrète, orientée usage, qui explique à l'utilisateur à quoi sert CE document précis — pas une description de son contenu, mais ce qu'il permet de FAIRE. Commence par une formulation naturelle du type "Ce document vous sert à...", "À utiliser pour...", ou équivalent.
  Exemples de ton attendu : "Checklist à suivre pour mener votre recrutement étape par étape.", "Email prêt à envoyer pour proposer le rendez-vous.", "Fiche de référence pour cadrer les critères de sélection avec votre équipe."
- "is_email_action_source" : true UNIQUEMENT si CE document EST le texte prêt à être envoyé comme email pour cette mission (un message complet adressé à quelqu'un, avec formule de politesse). Au maximum UN document peut avoir cette valeur à true parmi tous ceux que tu génères. false pour tous les autres (fiches, checklists, briefs, etc.).
- "contenu" : le document complet, rédigé et directement utilisable — jamais un résumé vague ni une liste de intentions. Utilise du markdown simple (titres avec # ou ##, listes avec "- ", texte important en **gras**). Adapte la longueur à la nature du document (une fiche de poste peut faire 200 à 400 mots, une checklist peut être plus courte mais doit rester complète et actionnable).
{DATE_RULE}
- RÈGLE ABSOLUE — aucun placeholder entre crochets : n'écris JAMAIS de texte entre crochets destiné à être rempli plus tard (ex. "[Nom de l'appli]", "[Insérer le budget]", "[votre email]", "[nom du destinataire]"). La synthèse de découverte fournie doit déjà contenir toutes les informations concrètes nécessaires — utilise-les telles quelles. Si une information précise venait malgré tout à manquer, formule le passage de façon générique et naturelle (ex. "l'application" au lieu de "[Nom de l'appli]") plutôt que d'exposer un crochet visible dans le document final.
- Signature d'un document email (is_email_action_source: true) : si "FAITS ÉTABLIS PENDANT LA DÉCOUVERTE" indique un nom d'expéditeur, signe avec ce nom exact. Sinon, signe avec une formule générique neutre ("L'équipe", par exemple) — ne signe JAMAIS avec un placeholder comme "[Votre prénom]" ou "[Nom]".
- Si un bloc "FAITS ÉTABLIS PENDANT LA DÉCOUVERTE" est fourni dans le message utilisateur, ces valeurs (dates, durées, noms, emails) priment sur toute autre déduction : utilise-les exactement telles quelles dans tous les documents, ne les contredis jamais d'un document à l'autre.
- Chaque document doit apporter une valeur différente des autres : ne duplique jamais le même contenu sous deux titres.
- Aucun texte hors du JSON. Pas de markdown autour du JSON, pas de blocs ```json, pas de commentaire."""


DOCUMENTS_INCREMENTAL_SYSTEM_PROMPT = f"""Tu es Orion, Chief of Staff IA. Cette mission a déjà des documents de travail générés précédemment ; un NOUVEAU besoin est apparu dans la conversation depuis (ex. la mission portait sur une réunion avec une personne, et l'utilisateur demande ensuite d'envoyer aussi un email à une AUTRE personne, ou une communication distincte).

Ta seule tâche : identifier ce nouveau besoin et créer le ou les documents qui lui correspondent — jamais recréer ou reformuler un document déjà existant qui couvre déjà un besoin identique.

MÉTHODE À SUIVRE avant de répondre, dans cet ordre :
1. Relis un par un les documents déjà existants (liste fournie ci-dessous) et note précisément CE QUE CHACUN COUVRE (quel destinataire, quel objet, quelle communication).
2. Relis les "FAITS ÉTABLIS PENDANT LA DÉCOUVERTE" (en particulier la liste des destinataires) et la synthèse de la conversation : y a-t-il un destinataire, un email, ou un besoin mentionné qui N'EST COUVERT PAR AUCUN document existant ?
3. Si OUI, crée un nouveau document pour CE besoin précis (0 à 2 documents). Si NON — tout ce qui est demandé est déjà couvert par un document existant, aucune information nouvelle — renvoie une liste VIDE.

Exemple concret (à traiter EXACTEMENT sur ce modèle) : les documents existants ne couvrent que l'email d'invitation à Thomas pour une réunion. La conversation indique ensuite qu'un email de suivi doit AUSSI être envoyé à Sophie, une personne différente. Aucun document existant ne couvre Sophie : tu DOIS créer un nouveau document email pour ce suivi à Sophie, avec "is_email_action_source": true — le fait qu'un autre document soit déjà marqué source d'email pour Thomas n'empêche PAS ce nouveau document de l'être aussi, puisqu'il s'agit d'un email différent, à un destinataire différent.

Réponds UNIQUEMENT avec un objet JSON strictement valide, de cette forme exacte :
{{"documents": [{{"titre": "...", "type": "...", "purpose": "...", "is_email_action_source": true ou false, "contenu": "..."}}, ...]}}

Règles :
- 0, 1 ou 2 nouveaux documents, jamais plus. Si les documents déjà existants couvrent déjà tout ce qui est demandé (rien de réellement nouveau), renvoie une liste VIDE ({{"documents": []}}).
- Ne recrée JAMAIS un document dont l'objet recouvre un document déjà existant (voir liste fournie), même sous un titre différent.
- "is_email_action_source" : true UNIQUEMENT si ce nouveau document EST le texte prêt à être envoyé comme email pour ce nouveau besoin précis. Un document précédent peut déjà porter ce flag pour un email DIFFÉRENT (destinataire ou objet différent) sans que cela t'empêche d'en marquer un nouveau ici — chaque email distinct a son propre document source. En revanche, si le nouveau besoin ne concerne PAS l'envoi d'un nouvel email, ne marque aucun nouveau document à true.
- "purpose" : une phrase courte et concrète orientée usage.
- "contenu" : le document complet, rédigé et directement utilisable — jamais un résumé vague. Markdown simple autorisé.
{DATE_RULE}
- RÈGLE ABSOLUE — aucun placeholder entre crochets : jamais de texte entre crochets destiné à être rempli plus tard.
- Signature d'un document email : si "FAITS ÉTABLIS PENDANT LA DÉCOUVERTE" indique un nom d'expéditeur, signe avec ce nom exact. Sinon, signe avec une formule générique neutre ("L'équipe") — jamais un placeholder comme "[Votre prénom]".
- Si un bloc "FAITS ÉTABLIS PENDANT LA DÉCOUVERTE" est fourni, ces valeurs priment sur toute autre déduction.
- Aucun texte hors du JSON. Pas de markdown autour du JSON, pas de blocs ```json, pas de commentaire."""


def generate_documents(
    mission_objectif: str | None,
    conversation_summary: str,
    tasks: list[dict],
    mission_facts: dict | None = None,
    existing_documents: list[dict] | None = None,
) -> list[dict]:
    """existing_documents (liste de {"titre", "type", "is_email_action_source"}) : si
    fourni et non vide, bascule en mode incrémental — les documents ont déjà été générés
    pour cette mission, et un nouveau besoin est apparu depuis. Renvoie alors UNIQUEMENT
    les documents à ajouter (0 à 2), sans dupliquer l'existant (voir generate_plan pour
    le même principe appliqué au plan)."""
    is_incremental = bool(existing_documents)
    tasks_text = (
        "\n".join(f"- {t['titre']} : {t.get('description', '')}" for t in tasks)
        or "(aucune tâche)"
    )
    facts_block = _format_mission_facts(mission_facts)
    existing_block = ""
    if is_incremental:
        # Pas de restriction sur is_email_action_source ici : un nouveau besoin peut très
        # bien être lui-même un nouvel email à envoyer (ex. suivi à un second destinataire)
        # même si un document précédent portait déjà ce flag pour un email DIFFÉRENT —
        # chaque round peut avoir son propre email source (voir _find_email_source_document
        # côté missions.py, qui associe chacun à l'action qui le consomme).
        existing_list = "\n".join(f"- [{d['type']}] {d['titre']}" for d in existing_documents)
        existing_block = f"Documents déjà existants (ne pas dupliquer) :\n{existing_list}\n\n"

    user_prompt = (
        f"Objectif de la mission : {mission_objectif or 'non précisé'}\n\n"
        f"Synthèse de la phase de découverte :\n{conversation_summary}\n\n"
        f"Plan d'action déjà généré :\n{tasks_text}\n\n"
        + existing_block
        + (f"{facts_block}\n\n" if facts_block else "")
        + (
            "Détermine si de nouveaux documents doivent être ajoutés, au format JSON demandé."
            if is_incremental
            else "Génère les documents de travail au format JSON demandé."
        )
    )
    messages = [
        {
            "role": "system",
            "content": DOCUMENTS_INCREMENTAL_SYSTEM_PROMPT if is_incremental else DOCUMENTS_SYSTEM_PROMPT,
        },
        {"role": "user", "content": user_prompt},
    ]

    try:
        response = _create_completion(messages, json_mode=True)
    except Exception:
        try:
            response = _create_completion(messages, json_mode=False)
        except Exception as exc:
            raise OrionAPIError(
                "La génération des documents a échoué après deux tentatives. Réessaie dans un instant."
            ) from exc

    raw = response.choices[0].message.content

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(match.group(0)) if match else {"documents": []}

    documents = data.get("documents", [])
    parsed_documents = [
        {
            "titre": d["titre"].strip(),
            "type": (d.get("type") or "document").strip().lower(),
            "purpose": (d.get("purpose") or "").strip(),
            "is_email_action_source": bool(d.get("is_email_action_source")),
            "contenu": (d.get("contenu") or "").strip(),
        }
        for d in documents
        if isinstance(d, dict) and d.get("titre") and d.get("contenu")
    ]

    # Garde-fou : si Qwen a malgré tout marqué plusieurs NOUVEAUX documents comme source
    # d'email dans CE même appel (la consigne l'interdit), ne garde que le premier pour
    # éviter toute ambiguïté au moment de construire l'action email correspondante (voir
    # _run_generate_actions). Un document d'un round PRÉCÉDENT peut légitimement porter ce
    # flag pour un email différent — ça ne restreint pas ce round-ci (voir docstring).
    seen_email_source = False
    for doc in parsed_documents:
        if doc["is_email_action_source"]:
            if seen_email_source:
                doc["is_email_action_source"] = False
            seen_email_source = True

    return parsed_documents


ACTION_SYSTEM_PROMPT_TEMPLATE = """Tu es Orion, Chief of Staff IA. À partir d'une mission déjà cadrée, planifiée et documentée, tu proposes les actions concrètes et immédiatement exécutables qui font avancer la mission.
{scope_instruction}
Évalue INDÉPENDAMMENT la pertinence de chacun de ces deux types d'action pour CETTE mission précise — les deux peuvent être pertinents en même temps, un seul peut l'être, ou aucun :

1. "email" — pertinent si la mission nécessite de contacter quelqu'un, envoyer une offre, relancer, informer.
2. "calendar_event" — pertinent si la mission nécessite de planifier une réunion, un entretien, un rendez-vous, un point d'équipe.

Exemple où les deux sont pertinents en même temps : organiser un entretien candidat → un email de confirmation au candidat ET un événement calendrier pour le rendez-vous, tous deux à proposer.

RÈGLE ABSOLUE — cohérence avec les documents déjà générés : les documents listés ci-dessous (avec leur résumé d'usage) font partie intégrante de la mission, pas de simples annexes. Si un document décrit ou implique explicitement un envoi, un événement ou une démarche concrète (ex. un résumé indiquant "email prêt à envoyer à l'entreprise X", "invitation à programmer avec Y", "relance à effectuer"), tu DOIS proposer l'action correspondante — sauf si une action équivalente existe déjà (voir plus bas). Ne laisse jamais un document décrire un envoi qui ne se traduit par aucune action : c'est exactement le genre d'incohérence que cette règle sert à éviter.

RÈGLE ABSOLUE pour proposer "calendar_event" : tu ne peux le proposer QUE si la conversation permet de déterminer une date ET une heure suffisamment précises pour créer un événement réel :
- soit une date/heure absolue explicite (ex. "le 20 mars à 14h") ;
- soit une date/heure relative mais précise, que tu calcules à partir de la date de référence ci-dessous (ex. "vendredi prochain à 10h", "dans 3 jours à 9h") — ce calcul est autorisé car il part d'une information réellement donnée par l'utilisateur, ce n'est pas une invention.
Si l'échéance est vague (ex. "bientôt", "dans les prochaines semaines", une date sans heure), tu NE DOIS PAS proposer de "calendar_event" — cela n'empêche pas de proposer un "email" si celui-ci reste pertinent par ailleurs.

RÈGLE ABSOLUE anti-hallucination sur les HEURES (aussi grave que pour les dates, voir aussi la règle sur les dates dans les règles communes ci-dessous) : n'invente JAMAIS une heure précise (ex. "10h", "14h30") qui n'a pas été donnée explicitement ou calculée à partir d'une information réelle de l'utilisateur. Une échéance SANS heure explicite (ex. "un appel de suivi sera programmé dans les 5 jours", sans heure précisée) ne justifie PAS d'inventer une heure plausible pour pouvoir créer un "calendar_event" quand même — dans ce cas, ne propose PAS de "calendar_event" du tout ; propose plutôt un "email" dont le texte mentionne explicitement "horaire à confirmer avec le destinataire" plutôt qu'une heure inventée.
  Exemple à ne JAMAIS faire : la synthèse mentionne "un appel de suivi sera programmé dans les 5 jours ouvrés" (aucune heure donnée) et tu proposes quand même un "calendar_event" avec "date_debut": "2026-07-17T10:00:00" (l'heure 10h00 est inventée, ce n'est écrit nulle part dans la conversation).
  Exemple correct : dans ce même cas, ne propose PAS de "calendar_event". Propose à la place un "email" dont le contenu dit par exemple : "Un appel de suivi sera programmé dans les 5 jours ouvrés ; nous vous proposerons un créneau — horaire à confirmer avec vous."

RÈGLE ABSOLUE : ne force jamais un type d'action qui n'a pas de sens pour cette mission juste pour en proposer deux. Si un seul type est réellement pertinent, ne retourne que celui-là. Si aucun n'est pertinent (ex. mission sans échéance ni contact à prévenir), retourne une liste vide.

Date de référence (aujourd'hui) : {reference_date}

Voici les tâches du plan déjà généré pour cette mission (id — titre) :
{tasks_list}

RÈGLE ABSOLUE sur "task_id" : chaque action doit accomplir directement UNE de ces tâches. Pour CHAQUE action que tu proposes, identifie laquelle et renvoie son id exact dans le champ "task_id" (nombre entier, pas de guillemets). N'invente jamais un id qui n'est pas dans la liste ci-dessus. Deux actions distinctes peuvent viser la même tâche ou des tâches différentes selon le contexte.
{existing_actions_block}
Réponds UNIQUEMENT avec un objet JSON strictement valide, de cette forme exacte :
{{"actions": [ {{...}}, {{...}} ]}}
La liste "actions" contient 0, 1 ou 2 éléments — jamais de doublon du même type, et jamais de doublon d'une action déjà existante listée ci-dessus (même destinataire/objet).

Pour un élément de type email, exactement :
{{"type": "email", "task_id": <id de la tâche concernée>, "destinataire": "...", "sujet": "...", "contenu": "..."}}

Pour un élément de type calendar_event, exactement :
{{"type": "calendar_event", "task_id": <id de la tâche concernée>, "titre": "...", "description": "...", "date_debut": "YYYY-MM-DDTHH:MM:SS", "date_fin": "YYYY-MM-DDTHH:MM:SS", "participant_email": "..." ou null}}

Règles communes à chaque action proposée :
- Déduis le destinataire (email) ou l'objet du rendez-vous (événement) le plus pertinent pour CETTE mission à partir du contexte réel — n'applique aucun gabarit fixe.
- RÈGLE ABSOLUE sur "participant_email" (calendar_event) — correspondance stricte, jamais de déduction : c'est l'adresse email de la personne AVEC QUI ce rendez-vous précis a lieu, UNIQUEMENT si "FAITS ÉTABLIS PENDANT LA DÉCOUVERTE" ou la conversation l'associent explicitement et sans ambiguïté à CET événement précis. Si la mission mentionne plusieurs destinataires pour des raisons différentes (ex. un rendez-vous avec l'un, un email à un autre pour un tout autre sujet), ne mets JAMAIS l'adresse d'une personne non concernée par CE rendez-vous, même si c'est la seule adresse disponible dans la mission. En cas de doute réel, laisse "participant_email" à null plutôt que de deviner — un événement sans participant_email certain sera traité indépendamment de tout email, jamais fusionné au hasard.
- RÈGLE ABSOLUE sur "email" — jamais d'adresse inventée ou de test : ne propose une action "email" QUE si une adresse email réelle et explicite est disponible pour ce destinataire (dans "FAITS ÉTABLIS PENDANT LA DÉCOUVERTE" ou dans la conversation). Si aucune adresse réelle n'est disponible pour un destinataire, NE PROPOSE PAS d'action "email" pour lui — même si un email semblerait par ailleurs pertinent, l'absence d'adresse réelle doit se traduire par l'absence de cette action, jamais par une adresse de test ou inventée. Le "calendar_event" reste proposable indépendamment si les conditions de date/heure ci-dessus sont réunies.
- Email "contenu" : complet, rédigé, prêt à être envoyé tel quel — pas un brouillon vague. Termine par une formule de politesse sobre. Signature : si "FAITS ÉTABLIS PENDANT LA DÉCOUVERTE" indique un nom d'expéditeur, signe avec ce nom exact ; sinon signe avec une formule générique neutre ("L'équipe") — ne signe JAMAIS avec un placeholder comme "[Votre prénom]" ou un nom inventé.
- RÈGLE ABSOLUE — ne jamais affirmer un fait non encore réalisé : au moment où ce texte est rédigé, RIEN n'a encore été envoyé ni créé (ni l'email, ni l'événement). N'écris donc JAMAIS de phrase au passé composé ou au présent affirmant une action déjà accomplie (ex. "j'ai créé l'événement dans l'agenda partagé", "l'invitation a été ajoutée à votre calendrier", "l'événement est dans l'agenda partagé"). Si l'email accompagne un rendez-vous, contente-toi d'inviter et de donner la date/l'heure, et mentionne simplement qu'une invitation à ajouter à l'agenda accompagne le message (ex. "Vous trouverez ci-joint une invitation pour l'ajouter à votre agenda"), sans jamais prétendre que cet ajout a déjà eu lieu.
- Événement "titre" : court et concret. "description" : une ou deux phrases de contexte. "date_fin" postérieure à "date_debut" (durée d'1 heure par défaut si aucune durée n'est précisée).
- Ton professionnel et direct dans tous les cas (pas de formules enthousiastes du type "Bien sûr !" ou "Avec plaisir !").
- N'invente JAMAIS une date calendaire ou une heure précise qui n'a pas été donnée explicitement ou calculée comme indiqué ci-dessus — que ce soit dans les champs d'un "calendar_event" ou mentionnée en toutes lettres dans le texte d'un "email". En cas de doute sur la date ou l'heure, ne propose pas de "calendar_event", et si un email mentionne un rendez-vous sans heure connue, écris "horaire à confirmer" plutôt que d'inventer une heure.
- RÈGLE ABSOLUE — aucun placeholder entre crochets : n'écris JAMAIS de texte entre crochets destiné à être rempli plus tard (ex. "[Nom de l'appli]", "[votre nom]", "[email dédié]", "[nom du destinataire]"). La synthèse de découverte fournie doit déjà contenir toutes les informations concrètes nécessaires — utilise-les telles quelles. Si une information précise venait malgré tout à manquer, formule le passage de façon générique et naturelle plutôt que d'exposer un crochet visible (sauf pour "destinataire" d'un email, où l'absence d'adresse réelle signifie qu'il ne faut proposer AUCUNE action email, voir règle ci-dessus).
- Si un bloc "FAITS ÉTABLIS PENDANT LA DÉCOUVERTE" est fourni dans le message utilisateur, ces valeurs (dates, heures, durées, noms, emails) priment sur toute autre déduction : utilise-les exactement telles quelles, ne les contredis jamais et n'en invente pas d'autres à la place.
- Aucun texte hors du JSON. Pas de markdown autour du JSON, pas de blocs ```json, pas de commentaire."""


def _strip_timezone_suffix(dt_str: str) -> str:
    # Garde-fou : create_calendar_event envoie date_debut/date_fin à Google accompagnés
    # d'un champ timeZone: "Europe/Paris" séparé, en s'attendant à des horaires "naïfs"
    # (sans fuseau). Si Qwen ajoutait malgré tout un suffixe "Z" ou "+HH:MM"/"-HH:MM",
    # Google l'interpréterait littéralement et IGNORERAIT le champ timeZone fourni à côté
    # — l'événement serait alors créé à une heure différente de celle affichée dans
    # l'app. On retire donc tout suffixe de fuseau avant stockage, par sécurité.
    return re.sub(r"(Z|[+-]\d{2}:\d{2})$", "", dt_str)


def _parse_action_item(data: dict, valid_task_ids: set[int]) -> dict | None:
    # task_id n'est retenu que s'il correspond à une tâche réellement fournie —
    # sinon on le laisse à None plutôt que de faire confiance aveuglément au modèle.
    raw_task_id = data.get("task_id")
    task_id = raw_task_id if isinstance(raw_task_id, int) and raw_task_id in valid_task_ids else None

    action_type = (data.get("type") or "").strip().lower()

    if action_type == "calendar_event":
        titre = (data.get("titre") or "").strip()
        description = (data.get("description") or "").strip()
        date_debut = _strip_timezone_suffix((data.get("date_debut") or "").strip())
        date_fin = _strip_timezone_suffix((data.get("date_fin") or "").strip())
        participant_email = (data.get("participant_email") or "").strip() or None

        if not (titre and date_debut and date_fin):
            return None

        return {
            "type": "calendar_event",
            "task_id": task_id,
            "titre": titre,
            "description": description,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "participant_email": participant_email,
        }

    if action_type == "email":
        destinataire = (data.get("destinataire") or "").strip()
        sujet = (data.get("sujet") or "").strip()
        contenu = (data.get("contenu") or "").strip()

        if not (destinataire and sujet and contenu):
            return None

        return {
            "type": "email",
            "task_id": task_id,
            "destinataire": destinataire,
            "sujet": sujet,
            "contenu": contenu,
        }

    return None


def propose_action(
    mission_objectif: str | None,
    conversation_summary: str,
    tasks: list[dict],
    documents: list[dict],
    mission_facts: dict | None = None,
    existing_actions: list[dict] | None = None,
) -> list[dict]:
    """existing_actions (liste de {"type", "destinataire"/"titre", "sujet"}) : si fourni
    et non vide, une ou plusieurs actions ont déjà été proposées pour cette mission — un
    nouveau besoin est apparu dans la conversation depuis. La liste est passée à Qwen pour
    qu'il ne propose jamais un doublon, et ne propose QUE ce qui répond au nouveau besoin
    (voir generate_plan/generate_documents pour le même principe)."""
    docs_text = (
        "\n".join(
            f"- [{d['type']}] {d['titre']}" + (f" — {d['purpose']}" if d.get("purpose") else "")
            for d in documents
        )
        or "(aucun document)"
    )
    tasks_list = (
        "\n".join(f"- {t['id']} — {t['titre']}" for t in tasks) or "(aucune tâche disponible)"
    )
    valid_task_ids = {t["id"] for t in tasks}
    facts_block = _format_mission_facts(mission_facts)

    scope_instruction = ""
    existing_actions_block = ""
    if existing_actions:
        existing_list = "\n".join(
            f"- {a['type']} — {a.get('destinataire') or a.get('titre') or ''}".strip()
            for a in existing_actions
        )
        existing_actions_block = f"\nActions déjà existantes pour cette mission :\n{existing_list}\n"
        scope_instruction = """
IMPORTANT — cette mission a déjà une ou plusieurs actions proposées précédemment (liste fournie plus bas) ; un NOUVEAU besoin est apparu dans la conversation depuis (ex. la mission portait sur une réunion avec une personne, et l'utilisateur demande ensuite d'envoyer aussi un email à une AUTRE personne). Ta tâche ICI n'est PAS de réévaluer toute la mission depuis le début, mais UNIQUEMENT de couvrir ce nouveau besoin précis.

MÉTHODE à suivre avant de répondre, dans cet ordre :
1. Pour CHAQUE action existante listée plus bas, note précisément ce qu'elle couvre déjà (quel destinataire, quel objet/rendez-vous).
2. Pour CHAQUE type d'action existant (email, calendar_event), vérifie si le nouveau besoin concerne EXACTEMENT le même destinataire/objet qu'une action déjà existante de ce type. Si OUI, ce besoin est déjà couvert : NE LA REPROPOSE PAS, même reformulée.
3. Ne propose une action que pour un destinataire ou un objet qui n'est couvert par AUCUNE action existante — même si une action du MÊME TYPE existe déjà pour un AUTRE destinataire, ce n'est PAS un doublon tant que le destinataire ou l'objet diffère.
4. Si le nouveau besoin ne concerne qu'UN SEUL des deux types (ex. seulement un nouvel email, pas un nouvel événement), ne renvoie QUE ce type — ne reproduis pas l'autre type juste pour "compléter" la réponse.

Exemple concret à suivre exactement sur ce modèle : une action email et une action calendar_event existent déjà, toutes deux liées à une réunion avec Thomas. La conversation indique qu'un email de suivi doit AUSSI être envoyé à Sophie, une personne différente — aucun nouvel événement n'est demandé. Propose UNIQUEMENT une NOUVELLE action email adressée à Sophie. Ne reproduis NI l'action email de Thomas NI l'action calendar_event de la réunion — elles existent déjà et ne changent pas.

Si le nouveau besoin est déjà entièrement couvert par les actions existantes, ou si la conversation n'introduit en réalité aucun besoin nouveau, renvoie une liste VIDE ({"actions": []}).
"""

    user_prompt = (
        f"Objectif de la mission : {mission_objectif or 'non précisé'}\n\n"
        f"Synthèse de la phase de découverte :\n{conversation_summary}\n\n"
        f"Documents déjà générés :\n{docs_text}\n\n"
        + (f"{facts_block}\n\n" if facts_block else "")
        + "Évalue indépendamment la pertinence d'un email et d'un événement calendrier pour cette"
        " mission, et retourne au format JSON demandé la liste des actions pertinentes (0, 1 ou 2),"
        " en identifiant pour chacune la tâche du plan qu'elle accomplit."
    )
    system_prompt = ACTION_SYSTEM_PROMPT_TEMPLATE.format(
        reference_date=_today_str(),
        tasks_list=tasks_list,
        scope_instruction=scope_instruction,
        existing_actions_block=existing_actions_block,
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    try:
        response = _create_completion(messages, json_mode=True)
    except Exception:
        try:
            response = _create_completion(messages, json_mode=False)
        except Exception as exc:
            raise OrionAPIError(
                "La proposition d'action a échoué après deux tentatives. Réessaie dans un instant."
            ) from exc

    raw = response.choices[0].message.content

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(match.group(0)) if match else {}

    raw_actions = data.get("actions")
    if not isinstance(raw_actions, list):
        raw_actions = []

    parsed = [_parse_action_item(item, valid_task_ids) for item in raw_actions if isinstance(item, dict)]
    actions = [a for a in parsed if a is not None]

    # Sécurité contre un doublon de type que le modèle aurait renvoyé malgré la consigne
    # ("jamais de doublon du même type") : on ne garde que la première occurrence de chaque type.
    seen_types = set()
    deduped = []
    for action in actions:
        if action["type"] in seen_types:
            continue
        seen_types.add(action["type"])
        deduped.append(action)

    return deduped

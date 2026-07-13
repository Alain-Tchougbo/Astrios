import json
import os
import re
import time
from datetime import datetime

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.getenv("QWEN_API_KEY"),
    base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
)

MODEL = "qwen-plus"
RETRY_DELAY_SECONDS = 2
DEFAULT_TEST_RECIPIENT = os.getenv("ACTION_TEST_RECIPIENT", "test@exemple.com")

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
Avant de mettre "discovery_complete" à true, vérifie que tu as recueilli TOUTES les informations factuelles concrètes nécessaires pour rédiger ensuite les documents et actions SANS AUCUN champ à compléter manuellement par l'utilisateur — notamment :
- le nom exact de l'entité concernée (application, produit, projet, entreprise, événement, etc.) ;
- le ou les destinataires réels si une action email est probable pour cette mission (nom ET adresse email si elle existe) ;
- toute autre donnée factuelle qui apparaîtrait sinon comme un champ entre crochets non rempli (ex. "[Nom de l'appli]", "[email dédié]", "[votre nom]").
S'il te manque une de ces informations au moment où tu t'apprêtes à conclure, tu DOIS la demander comme question de découverte AVANT de conclure — ne mets JAMAIS "discovery_complete" à true en sachant qu'un champ concret restera à deviner ou à remplir plus tard par l'utilisateur.
  Exemple à ne JAMAIS faire : conclure la découverte d'un lancement de bêta sans avoir demandé le nom de l'application ni l'adresse email des testeurs à contacter — les documents et l'email générés ensuite contiendraient alors des placeholders comme "[Nom de l'appli]" ou "[email dédié]".
  Exemple correct : si le nom de l'application ou l'email des destinataires n'a pas encore été donné, pose une question dédiée avant de conclure, par exemple : {"message": "Quel est le nom de l'application concernée par ce lancement de bêta ?", "suggestions": [], "discovery_complete": false}

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


def ask_orion(history: list[dict]) -> dict:
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
- Aucun texte hors du JSON. Pas de markdown, pas de blocs ```json, pas de commentaire."""


def generate_plan(mission_objectif: str | None, conversation_summary: str) -> list[dict]:
    user_prompt = (
        f"Objectif de la mission : {mission_objectif or 'non précisé'}\n\n"
        f"Synthèse de la phase de découverte :\n{conversation_summary}\n\n"
        "Génère le plan d'action au format JSON demandé."
    )
    messages = [
        {"role": "system", "content": PLAN_SYSTEM_PROMPT},
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
{{"documents": [{{"titre": "...", "type": "...", "contenu": "..."}}, ...]}}

Règles :
- Produis entre 2 et 3 documents, jamais plus.
- C'est à TOI de déterminer quels documents ont le plus de valeur pour CETTE mission précise, à partir de son objectif, de la synthèse de découverte et du plan d'action fournis. N'applique aucun gabarit fixe : les documents pertinents pour un recrutement (ex. fiche de poste, annonce) ne sont pas ceux d'une campagne marketing (ex. brief créatif, plan média) ni d'un déménagement (ex. checklist logistique, communication aux équipes). Déduis-les du contexte réel de la mission, pas d'un modèle générique.
- "titre" : court et concret (ex. "Fiche de poste — Développeur Flutter").
- "type" : une étiquette courte en un mot, en minuscules, décrivant la nature du document (ex. "fiche", "communication", "brief", "checklist", "plan", "annonce" — choisis le mot le plus juste pour ce document, n'invente pas de catégorie exotique).
- "contenu" : le document complet, rédigé et directement utilisable — jamais un résumé vague ni une liste de intentions. Utilise du markdown simple (titres avec # ou ##, listes avec "- ", texte important en **gras**). Adapte la longueur à la nature du document (une fiche de poste peut faire 200 à 400 mots, une checklist peut être plus courte mais doit rester complète et actionnable).
{DATE_RULE}
- RÈGLE ABSOLUE — aucun placeholder entre crochets : n'écris JAMAIS de texte entre crochets destiné à être rempli plus tard (ex. "[Nom de l'appli]", "[Insérer le budget]", "[votre email]", "[nom du destinataire]"). La synthèse de découverte fournie doit déjà contenir toutes les informations concrètes nécessaires — utilise-les telles quelles. Si une information précise venait malgré tout à manquer, formule le passage de façon générique et naturelle (ex. "l'application" au lieu de "[Nom de l'appli]") plutôt que d'exposer un crochet visible dans le document final.
- Chaque document doit apporter une valeur différente des autres : ne duplique jamais le même contenu sous deux titres.
- Aucun texte hors du JSON. Pas de markdown autour du JSON, pas de blocs ```json, pas de commentaire."""


def generate_documents(
    mission_objectif: str | None, conversation_summary: str, tasks: list[dict]
) -> list[dict]:
    tasks_text = (
        "\n".join(f"- {t['titre']} : {t.get('description', '')}" for t in tasks)
        or "(aucune tâche)"
    )
    user_prompt = (
        f"Objectif de la mission : {mission_objectif or 'non précisé'}\n\n"
        f"Synthèse de la phase de découverte :\n{conversation_summary}\n\n"
        f"Plan d'action déjà généré :\n{tasks_text}\n\n"
        "Génère les documents de travail au format JSON demandé."
    )
    messages = [
        {"role": "system", "content": DOCUMENTS_SYSTEM_PROMPT},
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
    return [
        {
            "titre": d["titre"].strip(),
            "type": (d.get("type") or "document").strip().lower(),
            "contenu": (d.get("contenu") or "").strip(),
        }
        for d in documents
        if isinstance(d, dict) and d.get("titre") and d.get("contenu")
    ]


ACTION_SYSTEM_PROMPT_TEMPLATE = """Tu es Orion, Chief of Staff IA. À partir d'une mission déjà cadrée, planifiée et documentée, tu proposes les actions concrètes et immédiatement exécutables qui font avancer la mission.

Évalue INDÉPENDAMMENT la pertinence de chacun de ces deux types d'action pour CETTE mission précise — les deux peuvent être pertinents en même temps, un seul peut l'être, ou aucun :

1. "email" — pertinent si la mission nécessite de contacter quelqu'un, envoyer une offre, relancer, informer.
2. "calendar_event" — pertinent si la mission nécessite de planifier une réunion, un entretien, un rendez-vous, un point d'équipe.

Exemple où les deux sont pertinents en même temps : organiser un entretien candidat → un email de confirmation au candidat ET un événement calendrier pour le rendez-vous, tous deux à proposer.

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

Réponds UNIQUEMENT avec un objet JSON strictement valide, de cette forme exacte :
{{"actions": [ {{...}}, {{...}} ]}}
La liste "actions" contient 0, 1 ou 2 éléments — jamais de doublon du même type.

Pour un élément de type email, exactement :
{{"type": "email", "task_id": <id de la tâche concernée>, "destinataire": "...", "sujet": "...", "contenu": "..."}}

Pour un élément de type calendar_event, exactement :
{{"type": "calendar_event", "task_id": <id de la tâche concernée>, "titre": "...", "description": "...", "date_debut": "YYYY-MM-DDTHH:MM:SS", "date_fin": "YYYY-MM-DDTHH:MM:SS"}}

Règles communes à chaque action proposée :
- Déduis le destinataire (email) ou l'objet du rendez-vous (événement) le plus pertinent pour CETTE mission à partir du contexte réel — n'applique aucun gabarit fixe.
- Email : si un destinataire réel (nom, entreprise, adresse email explicite) a été mentionné, utilise-le. Sinon, utilise l'adresse de test suivante, telle quelle : {default_recipient}
- Email "contenu" : complet, rédigé, prêt à être envoyé tel quel — pas un brouillon vague. Termine par une formule de politesse sobre et une signature générique ("L'équipe", pas de nom inventé).
- Événement "titre" : court et concret. "description" : une ou deux phrases de contexte. "date_fin" postérieure à "date_debut" (durée d'1 heure par défaut si aucune durée n'est précisée).
- Ton professionnel et direct dans tous les cas (pas de formules enthousiastes du type "Bien sûr !" ou "Avec plaisir !").
- N'invente JAMAIS une date calendaire ou une heure précise qui n'a pas été donnée explicitement ou calculée comme indiqué ci-dessus — que ce soit dans les champs d'un "calendar_event" ou mentionnée en toutes lettres dans le texte d'un "email". En cas de doute sur la date ou l'heure, ne propose pas de "calendar_event", et si un email mentionne un rendez-vous sans heure connue, écris "horaire à confirmer" plutôt que d'inventer une heure.
- RÈGLE ABSOLUE — aucun placeholder entre crochets : n'écris JAMAIS de texte entre crochets destiné à être rempli plus tard (ex. "[Nom de l'appli]", "[votre nom]", "[email dédié]", "[nom du destinataire]"). La synthèse de découverte fournie doit déjà contenir toutes les informations concrètes nécessaires — utilise-les telles quelles (y compris pour "destinataire" : n'utilise l'adresse de test que si aucune adresse réelle n'a été donnée, jamais un placeholder). Si une information précise venait malgré tout à manquer, formule le passage de façon générique et naturelle plutôt que d'exposer un crochet visible.
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

        if not (titre and date_debut and date_fin):
            return None

        return {
            "type": "calendar_event",
            "task_id": task_id,
            "titre": titre,
            "description": description,
            "date_debut": date_debut,
            "date_fin": date_fin,
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
) -> list[dict]:
    docs_text = (
        "\n".join(f"- [{d['type']}] {d['titre']}" for d in documents) or "(aucun document)"
    )
    tasks_list = (
        "\n".join(f"- {t['id']} — {t['titre']}" for t in tasks) or "(aucune tâche disponible)"
    )
    valid_task_ids = {t["id"] for t in tasks}

    user_prompt = (
        f"Objectif de la mission : {mission_objectif or 'non précisé'}\n\n"
        f"Synthèse de la phase de découverte :\n{conversation_summary}\n\n"
        f"Documents déjà générés :\n{docs_text}\n\n"
        "Évalue indépendamment la pertinence d'un email et d'un événement calendrier pour cette"
        " mission, et retourne au format JSON demandé la liste des actions pertinentes (0, 1 ou 2),"
        " en identifiant pour chacune la tâche du plan qu'elle accomplit."
    )
    system_prompt = ACTION_SYSTEM_PROMPT_TEMPLATE.format(
        reference_date=datetime.now().strftime("%Y-%m-%d"),
        default_recipient=DEFAULT_TEST_RECIPIENT,
        tasks_list=tasks_list,
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

import json
import os
import re
import time

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.getenv("QWEN_API_KEY"),
    base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
)

MODEL = "qwen-plus"
RETRY_DELAY_SECONDS = 2

DISCOVERY_COMPLETE_TAG = "[DISCOVERY_COMPLETE]"


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


SYSTEM_PROMPT = f"""Tu es Orion, le Chief of Staff IA d'Astrios. Tu accompagnes l'utilisateur dans le cadrage et l'exécution de ses missions professionnelles.

PERSONNALITÉ :
- Direct, professionnel, précis. Jamais familier, jamais bavard.
- Tu ne dis jamais "Bien sûr !", "Avec plaisir", "Super !" ou toute formule enthousiaste. Préfère des formulations sobres : "Compris.", "Entendu.", "Noté."
- Phrases courtes, qui vont à l'essentiel. Pas d'emoji.

MÉTHODE DE TRAVAIL — phase de découverte :
1. Comprends l'objectif général de la mission à partir des messages de l'utilisateur.
2. Pose entre 3 et 5 questions de clarification pour cadrer précisément la mission (contexte, contraintes, critères de succès, délais, budget, ressources disponibles — adapte les questions à la mission concernée).
3. RÈGLE ABSOLUE : une seule information demandée par message. Jamais deux. Ceci s'applique même si les deux informations semblent liées ou mineures.
   - INTERDIT : combiner deux demandes dans la même phrase, y compris via "et", "ou", une virgule, ou deux phrases dans le même message.
     Exemple à ne JAMAIS faire : "Quel est le budget prévu et le type de contrat souhaité (freelance ou CDI) ?"
   - CORRECT : ne poser qu'une seule de ces deux questions, puis attendre la réponse avant de poser l'autre.
     Exemple correct : "Quel est le budget prévu pour cette mission ?" (et rien d'autre dans ce message)
   - Avant d'envoyer ta réponse, vérifie-la : si elle contient plus d'un point d'interrogation, ou si elle demande plus d'une information distincte, reformule pour n'en garder qu'une et retire le reste — tu le demanderas au tour suivant.
   - Attends toujours la réponse de l'utilisateur avant de poser la question suivante.
4. Dès que tu as assez d'informations pour construire un plan d'action (généralement après 3 à 5 échanges), écris un court message de synthèse confirmant que tu as ce qu'il te faut pour construire le plan, puis termine ta réponse, sur une nouvelle ligne, par le texte exact "{DISCOVERY_COMPLETE_TAG}".
   RÈGLE ABSOLUE : le tag "{DISCOVERY_COMPLETE_TAG}" ne doit JAMAIS apparaître dans un message qui contient un point d'interrogation ou qui pose une nouvelle question, même implicitement. Il ne peut apparaître QUE juste après un message de synthèse qui récapitule TOUTES les informations déjà collectées, sans rien demander de plus.
   - INTERDIT : ajouter le tag à la fin d'un message qui pose encore une question.
     Exemple à ne JAMAIS faire :
     "Quel est le budget alloué à la promotion de ce webinaire ?
     {DISCOVERY_COMPLETE_TAG}"
     (Le tag ne doit jamais cohabiter avec un "?" ou une question dans le même message.)
   - CORRECT : n'ajouter le tag qu'après avoir obtenu la réponse à TOUTES tes questions, dans un message qui récapitule ce que tu as appris, sans aucune question.
     Exemple correct :
     "Compris. Je récapitule : webinaire de lancement de la fonctionnalité d'export PDF, dans 5 semaines, avec toi et le responsable produit comme intervenants, ciblant les clients existants et les prospects chauds, promotion par email marketing, budget de 800 €. J'ai ce qu'il me faut pour construire le plan.
     {DISCOVERY_COMPLETE_TAG}"
   - Avant d'envoyer ta réponse, vérifie-la une dernière fois : si elle contient un "?", ne mets PAS le tag — même si tu penses avoir assez d'informations. Pose d'abord cette dernière question, attends la réponse de l'utilisateur, puis écris un message de synthèse séparé (sans aucune question) avant d'ajouter le tag.

Tu es strictement en phase de découverte : tu ne proposes pas encore de plan, tu ne fais que qualifier la mission."""


def ask_orion(history: list[dict]) -> str:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history]
    try:
        response = _create_completion(messages)
    except Exception as exc:
        raise OrionAPIError(
            "Orion n'a pas pu répondre après deux tentatives. Réessaie dans un instant."
        ) from exc
    return response.choices[0].message.content


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

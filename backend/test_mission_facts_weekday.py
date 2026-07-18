"""Validation du calcul de jour de la semaine côté backend (voir orion._french_weekday
et orion._format_mission_facts) — bug rapporté : Qwen écrivait "vendredi 18 juillet
2026" pour une date qui tombe en réalité un samedi. Le jour de la semaine est
maintenant calculé côté backend (locale fr) et imposé au LLM via le bloc
"FAITS ÉTABLIS", plutôt que laissé au calcul (peu fiable) du LLM."""

import orion


def test_french_weekday_matches_calendar():
    # 2026-07-18 est un samedi (vérifié via datetime.date.strftime("%A")).
    assert orion._french_weekday("2026-07-18") == "samedi"
    assert orion._french_weekday("2026-07-17") == "vendredi"
    assert orion._french_weekday("2026-01-01") == "jeudi"


def test_french_weekday_handles_missing_or_invalid_date():
    assert orion._french_weekday(None) is None
    assert orion._french_weekday("") is None
    assert orion._french_weekday("pas une date") is None


def test_mission_facts_block_instructs_exact_weekday_usage():
    mission_facts = {
        "destinataires": [],
        "rendez_vous": [
            {"objet": "Réunion budget", "date": "2026-07-18", "heure": "14:00", "duree_minutes": 60}
        ],
        "entites": [],
        "delais": [],
        "contraintes": [],
        "sender_name": None,
    }
    block = orion._format_mission_facts(mission_facts)
    assert "samedi 2026-07-18" in block
    assert "utilise EXACTEMENT \"samedi 2026-07-18\"" in block
    assert "ne recalcule JAMAIS le jour de la semaine" in block


def test_mission_facts_block_omits_weekday_note_without_date():
    mission_facts = {
        "destinataires": [],
        "rendez_vous": [{"objet": "Suivi", "date": None, "heure": None, "duree_minutes": None}],
        "entites": [],
        "delais": [],
        "contraintes": [],
        "sender_name": None,
    }
    block = orion._format_mission_facts(mission_facts)
    assert "Jour de la semaine" not in block
    assert "date à confirmer" in block


if __name__ == "__main__":
    test_french_weekday_matches_calendar()
    test_french_weekday_handles_missing_or_invalid_date()
    test_mission_facts_block_instructs_exact_weekday_usage()
    test_mission_facts_block_omits_weekday_note_without_date()
    print("Tous les tests de jour de semaine passent.")

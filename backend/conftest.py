"""Configuration commune aux tests.

Deux problèmes réglés ici, qui rendaient toute la partie « base de données » de la
suite inexploitable :

1. Les tests appellent database.SessionLocal() directement, sans jamais créer les
   tables. Sur un poste où astrios.db n'existe pas encore (le fichier est ignoré par
   git), chaque test échouait sur « no such table: missions » — la moitié de la suite
   ne testait donc plus rien.
2. Ils visaient la VRAIE base de développement : les exécuter y laissait des missions
   et des actions de test mêlées aux données réelles.

On redirige donc la session vers une base SQLite temporaire, créée avec le schéma
complet et supprimée à la fin. Les tests eux-mêmes n'ont pas besoin d'être modifiés :
ils lisent database.SessionLocal au moment de l'appel, pas à l'import.
"""

import os
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import database
import models  # noqa: F401 - enregistre les tables sur Base.metadata avant create_all


@pytest.fixture(autouse=True, scope="session")
def temporary_database():
    handle, db_path = tempfile.mkstemp(suffix="-astrios-test.db")
    os.close(handle)

    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 30}
    )
    original_engine, original_session = database.engine, database.SessionLocal

    database.engine = engine
    database.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    database.Base.metadata.create_all(bind=engine)

    yield

    database.engine, database.SessionLocal = original_engine, original_session
    engine.dispose()
    os.unlink(db_path)

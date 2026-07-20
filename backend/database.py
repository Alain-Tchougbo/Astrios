import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

DB_PATH = os.path.join(os.path.dirname(__file__), "astrios.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

# timeout=30 : les endpoints FastAPI sont synchrones, donc exécutés dans un pool de
# threads — deux requêtes concurrentes qui écrivent (ex. deux approbations sur des
# missions différentes) peuvent se croiser. Par défaut SQLite abandonne immédiatement
# avec "database is locked" ; ici l'écrivain attend que le verrou se libère.
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False, "timeout": 30}
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record):
    """WAL : les lecteurs ne bloquent plus l'écrivain (et inversement), ce qui élimine
    l'essentiel des "database is locked" quand l'UI rafraîchit tâches/documents/actions
    pendant qu'une exécution écrit. busy_timeout double le timeout du driver côté
    moteur SQLite lui-même."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema_migrations():
    # Base.metadata.create_all() ne crée que les tables manquantes, jamais les
    # colonnes ajoutées à un modèle existant. Migration légère et idempotente pour
    # les quelques colonnes ajoutées après la création initiale de la base.
    with engine.connect() as conn:
        action_columns = [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(actions)")]
        if "execution_mode" not in action_columns:
            conn.exec_driver_sql("ALTER TABLE actions ADD COLUMN execution_mode VARCHAR")
            conn.commit()

        document_columns = [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(documents)")]
        if "purpose" not in document_columns:
            conn.exec_driver_sql("ALTER TABLE documents ADD COLUMN purpose TEXT")
            conn.commit()
        if "is_email_source" not in document_columns:
            conn.exec_driver_sql("ALTER TABLE documents ADD COLUMN is_email_source BOOLEAN")
            conn.commit()

        mission_columns = [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(missions)")]
        if "mission_facts" not in mission_columns:
            conn.exec_driver_sql("ALTER TABLE missions ADD COLUMN mission_facts JSON")
            conn.commit()

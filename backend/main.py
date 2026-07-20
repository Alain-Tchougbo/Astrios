import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import Base, engine, ensure_schema_migrations
from routers import auth, missions

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")

Base.metadata.create_all(bind=engine)
ensure_schema_migrations()

app = FastAPI(title="Astrios API")

# Origines autorisées, configurables via CORS_ORIGINS (liste séparée par des
# virgules). La valeur était codée en dur sur le serveur de dev Vite : tout
# déploiement où le frontend n'est pas servi sur la même origine que l'API voyait
# ses appels bloqués par le navigateur, sans indice côté serveur.
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(missions.router)
app.include_router(auth.router)


@app.get("/health")
def health():
    return {"status": "ok"}

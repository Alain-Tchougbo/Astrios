from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import Base, engine
from routers import missions

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Astrios API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(missions.router)


@app.get("/health")
def health():
    return {"status": "ok"}

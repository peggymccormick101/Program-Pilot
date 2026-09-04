import os

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.database import Base, SessionLocal, engine
from app.routers import workflow
from app.seed import seed_if_empty

Base.metadata.create_all(bind=engine)

with SessionLocal() as db:
    seed_if_empty(db)

app = FastAPI(title="Program Pilot API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(workflow.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


# Serve the built React app (frontend/dist) so the backend and frontend can
# run as a single deployed service. Registered after the API routes above,
# so /api/* is always matched first.
FRONTEND_DIST = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
)

if os.path.isdir(FRONTEND_DIST):
    assets_dir = os.path.join(FRONTEND_DIST, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}")
    def serve_spa(full_path: str):
        candidate = os.path.normpath(os.path.join(FRONTEND_DIST, full_path))
        # Guard against escaping FRONTEND_DIST via a crafted path.
        if (
            full_path
            and candidate.startswith(FRONTEND_DIST)
            and os.path.isfile(candidate)
        ):
            return FileResponse(candidate)
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"))

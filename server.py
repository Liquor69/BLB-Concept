from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from channels.gmail import delivery_status, router as gmail_router


ROOT = Path(__file__).parent
allowed_origins = [
    item.strip() for item in os.getenv("LANDING_ALLOWED_ORIGINS", "*").split(",") if item.strip()
]

app = FastAPI(title="BLB Concept Courses")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["POST"],
    allow_headers=["Content-Type"],
)
app.include_router(gmail_router)
app.mount("/courses", StaticFiles(directory=str(ROOT / "courses"), html=True), name="courses")


@app.get("/", include_in_schema=False)
def landing_index() -> FileResponse:
    return FileResponse(ROOT / "index.html")


@app.get("/health", include_in_schema=False)
def health() -> dict[str, object]:
    return {"ok": True, "gmail": delivery_status()}

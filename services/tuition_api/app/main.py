"""Tuition REST API service entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.routes import router as v1_router
from app.core.config import settings
from app.core.errors import install_error_handlers

DESCRIPTION = """
Tuition backend used by the University Mobile App, the Banking App, the Admin web site and the
AI Tuition Assistant. **All public traffic goes through the API Gateway (Kong)**.

Authentication: call `POST /api/v1/auth/token`, then click **Authorize** and paste the token.
Demo users: `s1001 / Passw0rd!` (student), `bankapp / Bank123!` (bank), `admin / Admin123!` (admin).
"""


def create_app(*, init_db: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if init_db:
            from app.db import Base, SessionLocal, engine
            Base.metadata.create_all(engine)
            if settings.seed_demo_data:
                from app.seed import seed
                with SessionLocal() as db:
                    seed(db)
        yield

    app = FastAPI(title="University Tuition API", version="1.0.0", description=DESCRIPTION,
                  docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json", lifespan=lifespan)
    install_error_handlers(app)
    app.include_router(v1_router, prefix="/api/v1")

    @app.get("/health", tags=["Ops"], include_in_schema=False)
    def health():
        return {"status": "ok"}

    return app


app = create_app()

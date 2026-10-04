from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.admin import router as admin_router
from app.api.v1.auth import router as auth_router
from app.api.v1.chat import router as chat_router
from app.api.v1.escalations import router as escalations_router
from app.api.v1.files import router as files_router
from app.api.v1.knowledge import router as knowledge_router
from app.api.v1.tickets import router as tickets_router
from app.core.config import settings
from app.core.database import Base, engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database tables on application startup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Cleanup database engine connections
    await engine.dispose()


app = FastAPI(
    title=settings.APP_NAME,
    description="Enterprise-grade AI Customer Service Platform backend with dynamic configuration, RAG, ticketing, and session isolation.",
    version="1.0.0",
    lifespan=lifespan
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register v1 Routers
app.include_router(auth_router, prefix=settings.API_V1_PREFIX)
app.include_router(chat_router, prefix=settings.API_V1_PREFIX)
app.include_router(knowledge_router, prefix=settings.API_V1_PREFIX)
app.include_router(tickets_router, prefix=settings.API_V1_PREFIX)
app.include_router(escalations_router, prefix=settings.API_V1_PREFIX)
app.include_router(files_router, prefix=settings.API_V1_PREFIX)
app.include_router(admin_router, prefix=settings.API_V1_PREFIX)


@app.get("/health", tags=["Health"])
async def root_health():
    return {
        "status": "ok",
        "service": settings.APP_NAME,
        "environment": settings.APP_ENV
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)

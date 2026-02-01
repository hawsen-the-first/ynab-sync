import os
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .config import get_settings
from .models.database import init_db
from .routers import csv_router, ynab_router, akahu_router, mappings_router
from .services.scheduler import initialize_scheduler, shutdown_scheduler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    settings = get_settings()
    
    # Ensure data directory exists
    data_dir = Path("./data")
    data_dir.mkdir(exist_ok=True)
    
    # Initialize database
    await init_db(settings.database_url)
    logger.info("Database initialized")
    
    # Initialize scheduler for Akahu sync
    await initialize_scheduler()
    logger.info("Scheduler initialized")
    
    yield
    
    # Shutdown
    await shutdown_scheduler()
    logger.info("Scheduler shut down")


# Create FastAPI app
app = FastAPI(
    title="YANB Sync",
    description="YNAB Transaction Import Application - Import transactions from CSV or Akahu to YNAB",
    version="1.0.0",
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify allowed origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routers
app.include_router(csv_router, prefix="/api")
app.include_router(ynab_router, prefix="/api")
app.include_router(akahu_router, prefix="/api")
app.include_router(mappings_router, prefix="/api")


# Health check endpoint
@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "yanb-sync"
    }


# Serve static files (frontend)
# Try multiple paths - local development vs Docker
frontend_paths = [
    Path(__file__).parent.parent.parent / "frontend",  # Local dev: backend/../frontend
    Path("/app/frontend"),  # Docker mounted path
    Path("./frontend"),  # Relative path
]

frontend_path = None
for fp in frontend_paths:
    if fp.exists() and (fp / "index.html").exists():
        frontend_path = fp
        break

if frontend_path:
    app.mount("/static", StaticFiles(directory=str(frontend_path)), name="static")
    
    @app.get("/")
    async def serve_frontend():
        """Serve the frontend application."""
        return FileResponse(str(frontend_path / "index.html"))
    
    # Serve JS files
    @app.get("/js/{filename:path}")
    async def serve_js(filename: str):
        """Serve JavaScript files."""
        file_path = frontend_path / "js" / filename
        if file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(frontend_path / "index.html"))
    
    # Serve CSS files
    @app.get("/css/{filename:path}")
    async def serve_css(filename: str):
        """Serve CSS files."""
        file_path = frontend_path / "css" / filename
        if file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(frontend_path / "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

"""Day 2 backend setup endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from ..core.config import BASE_DIR, ENV_FILE, get_settings
from ..core.rbac import validate_rbac_matrix

router = APIRouter(prefix="/day2", tags=["Day 2 - Backend Project Setup"])


PROJECT_DIRECTORIES = [
    {
        "path": "customergraph/api",
        "purpose": "API route files. Day 1 and Day 2 endpoints live here now.",
    },
    {
        "path": "customergraph/core",
        "purpose": "Core config, logging, RBAC, roles, modules, and project constants.",
    },
    {
        "path": "customergraph/db",
        "purpose": "Future database connection and migration helpers.",
    },
    {
        "path": "customergraph/models",
        "purpose": "Future database or graph model definitions.",
    },
    {
        "path": "customergraph/schemas",
        "purpose": "Future Pydantic request/response schemas.",
    },
    {
        "path": "customergraph/services",
        "purpose": "Future business logic services.",
    },
    {
        "path": "customergraph/agents",
        "purpose": "Future agentic AI backend modules.",
    },
]


def _path_status(relative_path: str) -> dict:
    """Return simple filesystem status for one project path."""
    path = BASE_DIR / relative_path
    return {
        "path": relative_path,
        "exists": path.exists(),
        "type": "directory" if path.is_dir() else "file" if path.is_file() else "missing",
    }


@router.get("/setup-status")
def get_day2_setup_status() -> dict:
    """Check whether Day 2 setup items are present."""
    settings = get_settings()
    directories = [_path_status(item["path"]) for item in PROJECT_DIRECTORIES]
    required_files = [
        _path_status("app.py"),
        _path_status("requirements.txt"),
        _path_status(".env.example"),
        _path_status("customergraph/core/config.py"),
        _path_status("customergraph/core/logging.py"),
    ]
    rbac_validation = validate_rbac_matrix()

    return {
        "ok": True,
        "day": 2,
        "title": "Backend Project Setup",
        "message": "Day 2 setup is ready. FastAPI, config, .env pattern, logging, and folders are available.",
        "settings": settings.public_dict(),
        "env_file_present": ENV_FILE.exists(),
        "directories": directories,
        "required_files": required_files,
        "rbac_validation": rbac_validation,
    }


@router.get("/project-structure")
def get_project_structure() -> dict:
    """Return the intended backend folder structure and purpose."""
    return {
        "ok": True,
        "day": 2,
        "backend_first_rule": "React will start after backend APIs and logic are stable.",
        "structure": PROJECT_DIRECTORIES,
    }


@router.get("/public-config")
def get_public_config() -> dict:
    """Return safe, non-secret configuration values."""
    return {
        "ok": True,
        "data": get_settings().public_dict(),
        "note": "Secret values like AUTH_SECRET_KEY and NEO4J_PASSWORD are intentionally not returned.",
    }


@router.get("/ping")
def ping_day2() -> dict:
    """Small endpoint to verify the Day 2 router is loaded."""
    return {"ok": True, "message": "Day 2 router is working"}

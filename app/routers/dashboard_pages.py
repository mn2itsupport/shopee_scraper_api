from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.routers.dashboard_api import require_admin

router = APIRouter(tags=["dashboard-pages"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/dashboard", response_class=HTMLResponse)
def admin_dashboard(request: Request, admin: None = Depends(require_admin)) -> HTMLResponse:
    return templates.TemplateResponse(request, "dashboard.html", {"mode": "admin", "api_key": ""})


@router.get("/dashboard/me", response_class=HTMLResponse)
def client_dashboard(request: Request, api_key: str = Query(...)) -> HTMLResponse:
    return templates.TemplateResponse(request, "dashboard.html", {"mode": "client", "api_key": api_key})

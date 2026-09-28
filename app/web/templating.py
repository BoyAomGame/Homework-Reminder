"""Shared Jinja2 environment for all server-rendered pages."""

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app import timeutil
from app.auth.security import issue_csrf_token

TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["bangkok"] = timeutil.to_bangkok
templates.env.filters["thai_date"] = timeutil.thai_date
templates.env.filters["thai_datetime"] = timeutil.thai_datetime

DAY_NAMES = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"]
templates.env.globals["day_names"] = DAY_NAMES


def render(request: Request, name: str, context: dict | None = None):
    """Render a template with the CSRF token always available to forms."""
    context = dict(context or {})
    context["csrf_token"] = issue_csrf_token(request.session)
    return templates.TemplateResponse(request, name, context)

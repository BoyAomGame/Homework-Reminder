"""Home page: assignment summary and bot status."""

from fastapi import APIRouter, Request

from app import timeutil
from app.auth.deps import CurrentUser, DbSession
from app.db.models import AssignmentStatus
from app.services import assignments as assignments_service
from app.services import bots as bots_service
from app.web.templating import render

router = APIRouter()


@router.get("/")
async def dashboard(request: Request, user: CurrentUser, session: DbSession):
    items = await assignments_service.list_for_user(session, user.id)
    pending = [a for a in items if a.status == AssignmentStatus.PENDING]
    overdue = [a for a in pending if assignments_service.is_overdue(a)]
    upcoming = [a for a in pending if a not in overdue][:5]
    bot = await bots_service.get_for_user(session, user.id)
    return render(
        request,
        "dashboard.html",
        {
            "user": user,
            "bot": bot,
            "pending_count": len(pending),
            "done_count": len(items) - len(pending),
            "overdue": overdue,
            "upcoming": upcoming,
            "now": timeutil.now_bangkok(),
        },
    )

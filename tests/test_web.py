"""Web-layer tests: auth, CSRF, CRUD, and cross-user isolation."""

import re
import uuid

import httpx
import pytest_asyncio
from httpx import ASGITransport

import main


def _csrf(html: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _register(client, email=None, password="password123") -> str:
    """Create an account, leave the client logged in, return its CSRF token."""
    email = email or f"{uuid.uuid4().hex[:12]}@test.local"
    token = _csrf((await client.get("/register")).text)
    response = await client.post(
        "/register",
        data={"email": email, "password": password, "csrf_token": token},
    )
    assert response.status_code == 303
    return token


async def test_anonymous_is_redirected_to_login(client):
    for path in ("/", "/assignments", "/timetable", "/settings"):
        response = await client.get(path)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


async def test_register_login_logout(client):
    email = f"{uuid.uuid4().hex[:12]}@test.local"
    token = await _register(client, email=email)
    assert (await client.get("/")).status_code == 200

    response = await client.post("/logout", data={"csrf_token": token})
    assert response.status_code == 303
    assert (await client.get("/")).status_code == 303  # logged out

    token = _csrf((await client.get("/login")).text)
    response = await client.post(
        "/login", data={"email": email, "password": "wrong-password", "csrf_token": token}
    )
    assert "อีเมลหรือรหัสผ่านไม่ถูกต้อง" in response.text

    response = await client.post(
        "/login", data={"email": email, "password": "password123", "csrf_token": token}
    )
    assert response.status_code == 303


async def test_only_first_account_can_register(client):
    token = _csrf((await client.get("/register")).text)
    response = await client.post(
        "/register", data={"email": "x@y.com", "password": "short", "csrf_token": token}
    )
    assert "อย่างน้อย 8 ตัวอักษร" in response.text
    email = f"{uuid.uuid4().hex[:12]}@test.local"
    token = await _register(client, email=email)
    await client.post("/logout", data={"csrf_token": token})

    assert (await client.get("/register")).headers["location"] == "/login"
    token = _csrf((await client.get("/login")).text)
    response = await client.post(
        "/register", data={"email": "other@test.local", "password": "password123", "csrf_token": token}
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


async def test_post_without_csrf_token_is_rejected(client):
    await _register(client)
    response = await client.post(
        "/assignments", data={"subject_name": "เลข", "due_date": "2026-07-20"}
    )
    assert response.status_code == 403


async def test_assignment_crud_and_overdue_highlight(client):
    token = await _register(client)
    response = await client.post("/assignments", data={
        "subject_name": "คณิตศาสตร์", "due_date": "2020-01-01",  # long overdue
        "location": "หน้า 5", "csrf_token": token,
    })
    assert response.status_code == 303

    page = (await client.get("/assignments")).text
    assert "คณิตศาสตร์" in page and 'class="overdue"' in page

    match = re.search(r"/assignments/(\d+)/edit", page)
    aid = match.group(1)
    response = await client.post(f"/assignments/{aid}/edit", data={
        "subject_name": "คณิตศาสตร์", "due_date": "2030-01-01",
        "due_time": "08:30", "csrf_token": token,
    })
    assert response.status_code == 303
    page = (await client.get("/assignments")).text
    assert 'class="overdue"' not in page and "08:30" in page

    response = await client.post(
        f"/assignments/{aid}/status", data={"done": "1", "csrf_token": token}
    )
    assert response.status_code == 303
    assert "ยกเลิกเสร็จ" in (await client.get("/assignments")).text

    response = await client.post(
        f"/assignments/{aid}/delete", data={"csrf_token": token}
    )
    assert response.status_code == 303
    assert "ยังไม่มีการบ้าน" in (await client.get("/assignments")).text


async def test_invalid_form_shows_error_not_500(client):
    token = await _register(client)
    response = await client.post("/assignments", data={
        "subject_name": "เลข", "due_date": "not-a-date", "csrf_token": token,
    })
    assert response.status_code == 200
    assert "วันที่ส่งไม่ถูกต้อง" in response.text


async def test_reminder_offsets_validation(client):
    token = await _register(client)
    response = await client.post(
        "/settings/reminders", data={"offsets": "2880, 60", "csrf_token": token}
    )
    assert response.status_code == 303
    assert "2880, 60" in (await client.get("/settings")).text

    response = await client.post(
        "/settings/reminders", data={"offsets": "abc", "csrf_token": token}
    )
    assert "จำนวนนาทีคั่นด้วยจุลภาค" in response.text


async def test_bot_token_page_never_shows_token(client):
    token = await _register(client)
    fake_bot_token = "MTA" + "x" * 60
    response = await client.post(
        "/settings/bot-token", data={"bot_token": fake_bot_token, "csrf_token": token}
    )
    assert response.status_code == 303
    page = (await client.get("/settings")).text
    assert fake_bot_token not in page  # stored encrypted, never redisplayed
    assert "ส่งรหัสนี้ทางข้อความส่วนตัว" in page  # link code offered

    response = await client.post(
        "/settings/bot-token", data={"bot_token": "too short", "csrf_token": token}
    )
    assert "โทเคนบอต Discord ดูไม่ถูกต้อง" in response.text


async def test_ai_settings_are_saved_encrypted_and_apply_immediately(client):
    from app.db.models import AppSettings
    from app.db.session import SessionFactory
    from app.providers.registry import get_text_llm

    token = await _register(client)
    key = "test-secret-key"
    response = await client.post("/settings/ai", data={
        "csrf_token": token, "text_api_key": key,
        "text_base_url": "https://example.test/v1", "text_model": "example-model",
        "vision_model": "vision-model",
    })
    assert response.status_code == 303
    page = (await client.get("/settings")).text
    assert key not in page
    async with SessionFactory() as session:
        row = await session.get(AppSettings, 1)
        assert key not in row.text_api_key_encrypted
    llm = get_text_llm()
    assert llm._api_key == key
    assert llm._model == "example-model"

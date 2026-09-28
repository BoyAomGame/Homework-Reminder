from app.auth.security import (
    check_csrf_token,
    decrypt_bot_token,
    encrypt_bot_token,
    hash_password,
    issue_csrf_token,
    verify_password,
)


def test_password_hash_roundtrip():
    digest = hash_password("secret-password")
    assert digest != "secret-password"
    assert verify_password(digest, "secret-password")
    assert not verify_password(digest, "wrong-password")


def test_bot_token_encryption_roundtrip():
    token = "MTIzNDU2Nzg5.fake.discord-token"
    stored = encrypt_bot_token(token)
    assert token not in stored  # actually encrypted, not encoded
    assert decrypt_bot_token(stored) == token


def test_csrf_token_lifecycle():
    session: dict = {}
    token = issue_csrf_token(session)
    assert issue_csrf_token(session) == token  # stable per session
    assert check_csrf_token(session, token)
    assert not check_csrf_token(session, "forged")
    assert not check_csrf_token(session, None)
    assert not check_csrf_token({}, token)

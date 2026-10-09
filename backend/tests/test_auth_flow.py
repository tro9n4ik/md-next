from datetime import datetime
from types import SimpleNamespace

import pytest
import pyotp
from httpx import AsyncClient, ASGITransport
from app.main import app

@pytest.mark.asyncio
async def test_rate_limit_by_ip(auth_headers):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 5 неудачных попыток с IP A
        for _ in range(5):
            res = await ac.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": "WrongPassword"},
                headers={"X-Real-IP": "10.0.0.1"}
            )
            assert res.status_code == 401

        # 6-я попытка с IP A блокируется 429
        res = await ac.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "WrongPassword"},
            headers={"X-Real-IP": "10.0.0.1"}
        )
        assert res.status_code == 429

        # Попытка с IP B проходит (401 неверный пароль, но не 429)
        res_b = await ac.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "WrongPassword"},
            headers={"X-Real-IP": "10.0.0.2"}
        )
        assert res_b.status_code == 401

@pytest.mark.asyncio
async def test_2fa_setup_and_safe_change(auth_headers, monkeypatch):
    # Keep generation and verification in one TOTP window, including slow CI.
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 1, 1, 12, 0, 15, tzinfo=tz)

    monkeypatch.setattr(pyotp.totp, "datetime", SimpleNamespace(datetime=FrozenDatetime))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Первичный 2FA setup
        setup_res = await ac.post("/api/v1/auth/2fa/setup", headers=auth_headers)
        assert setup_res.status_code == 200
        sec1 = setup_res.json()["secret"]

        # Подтверждение первичного setup
        code1 = pyotp.TOTP(sec1).now()
        ver_res = await ac.post("/api/v1/auth/2fa/verify", json={"code": code1}, headers=auth_headers)
        assert ver_res.status_code == 200

        # Повторный setup без current_code запрещен (400)
        re_setup_bad = await ac.post("/api/v1/auth/2fa/setup", json={}, headers=auth_headers)
        assert re_setup_bad.status_code == 400

        # Повторный setup с верным current_code
        curr_code = pyotp.TOTP(sec1).now()
        re_setup_ok = await ac.post("/api/v1/auth/2fa/setup", json={"current_code": curr_code}, headers=auth_headers)
        assert re_setup_ok.status_code == 200
        sec2 = re_setup_ok.json()["secret"]
        assert sec2 != sec1

        # Старый код всё ещё валиден для логина до подтверждения нового секрета
        old_login_code = pyotp.TOTP(sec1).now()
        login_res = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "StrongTestPassword123!", "totp_code": old_login_code})
        assert login_res.status_code == 200

        # Подтверждение нового 2FA секрета по sec2
        code2 = pyotp.TOTP(sec2).now()
        ver2_res = await ac.post("/api/v1/auth/2fa/verify", json={"code": code2}, headers=auth_headers)
        assert ver2_res.status_code == 200

        # После подтверждения нового секрета старый код перестает работать
        old_code_after = pyotp.TOTP(sec1).now()
        fail_login = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "StrongTestPassword123!", "totp_code": old_code_after})
        assert fail_login.status_code == 401

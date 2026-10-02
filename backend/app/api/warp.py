import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.auth import get_current_user
from app.db.database import get_db
from app.services.warp import WarpService
from app.services.client_service import ClientService
from app.services import warp_presets
from app.models.setting import Setting
from app.services.events import log_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/warp", tags=["Cloudflare WARP / Gemini"], dependencies=[Depends(get_current_user)])


class GeminiRequest(BaseModel):
    prompt: str
    api_key: str | None = None


class WarpModeRequest(BaseModel):
    mode: Literal["proxy", "warp"]
    port: int = Field(default=40000, ge=1, le=65535)


class WarpLicenseRequest(BaseModel):
    key: str = Field(min_length=8, max_length=256)


class WarpUsageRequest(BaseModel):
    usage: Literal["off", "rules", "all"]


class WarpPresetRequest(BaseModel):
    key: str = Field(min_length=1, max_length=64)


def _require_cli() -> None:
    if not WarpService.cli_path():
        raise HTTPException(status_code=503, detail="warp-cli не установлен. Установите пакет cloudflare-warp.")


def _command_error(message: str):
    log_event("error", "warp", "Команда WARP завершилась ошибкой", {"reason": message[:400]})
    raise HTTPException(status_code=502, detail=f"Не удалось выполнить команду WARP: {message}")


@router.get("/status")
async def warp_status(db: AsyncSession = Depends(get_db)):
    return await WarpService.status(db)


@router.post("/register")
async def register_warp():
    _require_cli()
    success, message = await WarpService.register()
    if not success:
        _command_error(message)
    log_event("info", "warp", "Устройство WARP зарегистрировано")
    return {"status": "ok", "message": message}


@router.post("/connect")
async def connect_warp():
    _require_cli()
    success, message = await WarpService.connect()
    if not success:
        _command_error(message)
    log_event("info", "warp", "WARP подключён")
    return {"status": "ok", "message": message}


@router.post("/disconnect")
async def disconnect_warp():
    _require_cli()
    success, message = await WarpService.disconnect()
    if not success:
        _command_error(message)
    log_event("info", "warp", "WARP отключён")
    return {"status": "ok", "message": message}


@router.post("/mode")
async def set_warp_mode(request: WarpModeRequest, db: AsyncSession = Depends(get_db)):
    _require_cli()
    previous_port = await WarpService.get_port(db)
    previous_mode_setting = (await db.execute(select(Setting).where(Setting.key == "warp.mode"))).scalar_one_or_none()
    previous_mode = previous_mode_setting.value if previous_mode_setting and previous_mode_setting.value in ("proxy", "warp") else "proxy"
    success, message = await WarpService.set_mode(db, request.mode, request.port)
    if not success:
        _command_error(message)
    applied, reason = await ClientService.sync_xray_clients(db)
    if not applied:
        reverted, revert_reason = await WarpService.set_mode(db, previous_mode, previous_port)
        if reverted:
            await ClientService.sync_xray_clients(db)
        else:
            logger.error("Не удалось восстановить прежние настройки прокси WARP: %s", revert_reason)
        raise HTTPException(status_code=502, detail=f"Не удалось применить режим WARP в конфигурации Xray: {reason}")
    log_event("info", "warp", "Изменены режим WARP и порт SOCKS5", {"mode": request.mode, "port": request.port})
    return {"status": "ok", "mode": request.mode, "port": request.port, "message": message}


@router.get("/usage")
async def get_warp_usage(db: AsyncSession = Depends(get_db)):
    setting = (await db.execute(select(Setting).where(Setting.key == "warp.usage"))).scalar_one_or_none()
    return {"usage": setting.value if setting else "off"}


@router.put("/usage")
async def set_warp_usage(request: WarpUsageRequest, db: AsyncSession = Depends(get_db)):
    setting = (await db.execute(select(Setting).where(Setting.key == "warp.usage"))).scalar_one_or_none()
    if setting is None:
        setting = Setting(key="warp.usage", value=request.usage)
        db.add(setting)
    else:
        setting.value = request.usage
    await db.flush()
    success, message = await ClientService.sync_xray_clients(db)
    if not success:
        await db.rollback()
        code = 400 if any(asset in message.lower() for asset in ("geosite.dat", "geoip.dat")) else 502
        raise HTTPException(status_code=code, detail=message)
    await db.commit()
    log_event("info", "warp", "Изменён режим использования WARP в Xray", {"usage": request.usage})
    return {"usage": request.usage, "message": "Режим использования WARP обновлён, конфигурация Xray применена"}


@router.post("/license")
async def set_warp_license(request: WarpLicenseRequest):
    _require_cli()
    success, message = await WarpService.set_license(request.key)
    if not success:
        _command_error(message)
    logger.info("Лицензия WARP+ отправлена")
    log_event("info", "warp", "Лицензия WARP+ применена")
    return {"status": "ok", "message": message}


@router.post("/test")
async def test_warp(db: AsyncSession = Depends(get_db)):
    _require_cli()
    port = await WarpService.get_port(db)
    try:
        result = await WarpService.test_proxy(port)
    except Exception as exc:
        logger.warning("Ошибка проверки прокси WARP на порту %s: %s", port, exc)
        raise HTTPException(status_code=502, detail="Не удалось проверить WARP через SOCKS5. Убедитесь, что WARP подключён и работает в режиме proxy.") from exc
    return result


@router.post("/setup", status_code=status.HTTP_200_OK)
async def setup_warp(db: AsyncSession = Depends(get_db)):
    _require_cli()
    success, message = await WarpService.setup_warp_proxy(db)
    if not success:
        _command_error(message)
    applied, reason = await ClientService.sync_xray_clients(db)
    if not applied:
        raise HTTPException(status_code=502, detail=f"WARP подключён, но не удалось применить конфигурацию Xray: {reason}")
    log_event("info", "warp", "Бесплатный WARP включён и проверен через SOCKS5")
    return {"status": "ok", "message": message}


@router.get("/presets")
async def list_warp_presets(db: AsyncSession = Depends(get_db)):
    """Каталог пресетов и состояние каждого: что уже создано, чего не хватает."""
    state = await warp_presets.preset_state(db)
    return {
        "presets": [
            {
                "key": preset.key,
                "title": preset.title,
                "description": preset.description,
                "domains": preset.domains,
                "state": state[preset.key],
            }
            for preset in warp_presets.list_presets()
        ],
    }


@router.post("/presets/apply")
async def apply_warp_preset(request: WarpPresetRequest, db: AsyncSession = Depends(get_db)):
    """Создаёт правила пресета, включает WARP по правилам и применяет конфигурацию Xray.

    Повторное применение не дублирует правила и не трогает созданные вручную.
    """
    try:
        preset = warp_presets.get_preset(request.key)
    except KeyError:
        raise HTTPException(status_code=404, detail="Такого пресета нет. Доступные: " + ", ".join(warp_presets.PRESET_ORDER))

    enabled_automatically = await warp_presets.ensure_warp_rules_enabled(db)
    result = await warp_presets.apply_preset(db, preset.key)

    applied, reason = await ClientService.sync_xray_clients(db)
    if not applied:
        await db.rollback()
        code = 400 if any(token in reason.lower() for token in ("geosite.dat", "geoip.dat", "warp")) else 502
        logger.error("Не удалось применить набор правил WARP %s: %s", preset.key, reason)
        raise HTTPException(status_code=code, detail=f"Не удалось применить пресет «{preset.title}»: {reason}")

    await db.commit()
    log_event("info", "warp", "Применён пресет маршрутизации через WARP", {
        "preset": preset.key,
        "created": len(result["created"]),
        "removed": len(result["removed"]),
        "warp_enabled_automatically": enabled_automatically,
    })
    return {
        "status": "ok",
        **result,
        "warp_usage": "rules" if enabled_automatically else None,
    }


@router.post("/presets/remove")
async def remove_warp_preset(request: WarpPresetRequest, db: AsyncSession = Depends(get_db)):
    """Удаляет правила пресета и применяет конфигурацию Xray."""
    try:
        preset = warp_presets.get_preset(request.key)
    except KeyError:
        raise HTTPException(status_code=404, detail="Такого пресета нет. Доступные: " + ", ".join(warp_presets.PRESET_ORDER))

    result = await warp_presets.remove_preset(db, preset.key)
    if result["removed"]:
        applied, reason = await ClientService.sync_xray_clients(db)
        if not applied:
            await db.rollback()
            logger.error("Не удалось удалить набор правил WARP %s: %s", preset.key, reason)
            raise HTTPException(status_code=502, detail=f"Не удалось применить удаление пресета «{preset.title}»: {reason}")
    await db.commit()
    log_event("info", "warp", "Удалён пресет маршрутизации через WARP", {"preset": preset.key, "removed": len(result["removed"])})
    return {"status": "ok", **result}


@router.post("/gemini/generate", status_code=status.HTTP_200_OK)
async def proxy_gemini_request(req: GeminiRequest, db: AsyncSession = Depends(get_db)):
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent"
    if req.api_key:
        url += f"?key={req.api_key}"
    payload = {"contents": [{"parts": [{"text": req.prompt}]}]}
    try:
        res = await WarpService.fetch_via_warp(url, method="POST", json_data=payload, port=await WarpService.get_port(db))
        res.raise_for_status()
        return res.json()
    except Exception as exc:
        logger.warning("Ошибка запроса Gemini через WARP (%s)", type(exc).__name__)
        raise HTTPException(status_code=502, detail="Ошибка обращения к Gemini через WARP") from exc

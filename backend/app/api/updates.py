from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, SecretStr
import httpx

from app.api.auth import get_current_user
from app.services.component_status import get_components
from app.services.panel_updates import get_status, check_update, start_update

router = APIRouter(prefix='/api/v1/system/updates', tags=['Обновление панели'], dependencies=[Depends(get_current_user)])

class CheckRequest(BaseModel):
    github_token: SecretStr | None = Field(default=None, max_length=512)

class InstallRequest(CheckRequest):
    commit: str = Field(pattern=r'^[0-9a-f]{40}$')
    confirm: bool = False

@router.get('')
async def status():
    return await get_status()

@router.get('/components')
async def components():
    return await get_components()


@router.post('/check')
async def check(request: CheckRequest):
    try:
        return await check_update(request.github_token.get_secret_value() if request.github_token else '')
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    except httpx.HTTPError:
        raise HTTPException(502, 'Не удалось проверить обновления на GitHub. Попробуйте позже.') from None

@router.post('/install', status_code=202)
async def install(request: InstallRequest):
    if not request.confirm:
        raise HTTPException(400, 'Подтвердите обновление с резервной копией.')
    try:
        return await start_update(request.commit, request.github_token.get_secret_value() if request.github_token else '')
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    except (RuntimeError, OSError, httpx.HTTPError):
        raise HTTPException(503, 'Не удалось начать обновление. Текущая панель сохранена.') from None

import asyncio
import json
import re
import ipaddress
import base64
import hashlib
import socket
from pathlib import Path
from fastapi import APIRouter, Depends, Response, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, SecretStr, Field
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_admin
from app.db.database import get_db
from app.services.migration import source_report
from app.services.privileges import enabled, call

class MigrationRoute(APIRoute):
    def get_route_handler(self):
        handler=super().get_route_handler()
        async def safe_handler(request):
            try:return await handler(request)
            except RequestValidationError:
                # Pydantic's default errors include the raw input, including
                # SecretStr values that failed validation. Never echo it.
                raise HTTPException(422,{'code':'MIG_INPUT','message':'Проверьте адрес, порт, пароль комплекта (12–256 символов) и обязательные поля.'}) from None
        return safe_handler


router = APIRouter(prefix='/api/v1/migration', tags=['Переезд'], dependencies=[Depends(require_admin)],route_class=MigrationRoute)
bundle_lock = asyncio.Lock()
BUNDLE_DIR = Path(__file__).resolve().parents[2]/'migrations'


def operation_error(output, status, default='MIG_INTERNAL'):
    try:
        detail=json.loads(output)
        if not isinstance(detail,dict) or not re.fullmatch(r'MIG_[A-Z_]+',detail.get('code','')):raise ValueError()
        detail={'code':detail['code'],'message':detail['message']}
    except (ValueError,TypeError,KeyError):detail={'code':default,'message':'Операция не завершена. Проверьте этап и состояние серверов.'}
    return HTTPException(status,detail)


class BundleRequest(BaseModel):
    password: SecretStr = Field(min_length=12, max_length=256)


class TargetRequest(BaseModel):
    host: str = Field(max_length=45)
    port: int = Field(default=22, ge=1, le=65535)


class JobRequest(TargetRequest):
    action: str = Field(pattern='^(prepare|finalize|activate|commit|cancel|reconcile)$')
    fingerprint: str = Field(pattern=r'^SHA256:[A-Za-z0-9+/]{43}$')
    ssh_password: SecretStr | None = Field(default=None,min_length=1,max_length=512)
    password: SecretStr | None = Field(default=None,min_length=12,max_length=256)
    email: str = Field(default='', max_length=254)
    confirm: bool = False
    confirm_protocol_tests: bool = False


def public_host(host):
    try:
        ip=ipaddress.ip_address(host)
        if not ip.is_global or ip.is_multicast or ip.is_reserved or getattr(ip,'scope_id',None): raise ValueError()
        return str(ip)
    except ValueError:raise HTTPException(400, 'Укажите публичный IP нового VPS') from None


@router.post('/fingerprint')
async def target_fingerprint(request: TargetRequest, response: Response):
    host=public_host(request.host)
    def probe():
        import paramiko
        sock=socket.create_connection((host,request.port),timeout=10)
        transport=paramiko.Transport(sock)
        try:
            transport.banner_timeout=10; transport.start_client(timeout=10)
            key=transport.get_remote_server_key()
            return {'host':host,'port':request.port,'fingerprint':'SHA256:'+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('='),
                    'verified':False,'message':'Сверьте отпечаток с консолью или данными провайдера. Получение отпечатка само по себе не подтверждает подлинность сервера.'}
        finally:transport.close();sock.close()
    try:result=await asyncio.to_thread(probe)
    except Exception:raise HTTPException(502,{'code':'MIG_SSH_CONNECT','message':'Не удалось получить SSH-ключ. Проверьте IP, порт и доступность сервера.'}) from None
    response.headers['Cache-Control']='no-store'
    return result


@router.get('/jobs')
async def job_status(response: Response):
    response.headers['Cache-Control']='no-store'
    if not enabled():return {'phase':'unavailable','message':'Переезд запускается на серверной установке с системным помощником.','source_fenced':False,'activation_started':False}
    code,output,_=await call('migration-status',timeout=15)
    if code:raise operation_error(output,503)
    return json.loads(output)


@router.post('/jobs',status_code=202)
async def start_job(request: JobRequest, response: Response, current_user=Depends(require_admin)):
    public_host(request.host)
    if not request.confirm or request.action in {'activate','commit'} and not request.confirm_protocol_tests:
        raise HTTPException(400,'Подтвердите действие и внешнюю проверку протоколов перед активацией')
    if not enabled():raise HTTPException(503,'Нужен системный помощник серверной панели')
    payload=request.model_dump();payload['ssh_password']=request.ssh_password.get_secret_value() if request.ssh_password else None;payload['password']=request.password.get_secret_value() if request.password else None
    payload['actor_id']=current_user.id
    code,output,_=await call('migration-start',request=payload,timeout=25)
    if code:raise operation_error(output,409)
    response.headers['Cache-Control']='no-store'
    return json.loads(output)


@router.get('/packages')
async def list_packages(response: Response):
    response.headers['Cache-Control']='no-store'
    return [{'name':p.name,'size':p.stat().st_size,'created_at':p.stat().st_mtime} for p in BUNDLE_DIR.glob('*.mdmove')
            if re.fullmatch(r'[0-9a-f]{32}\.mdmove',p.name) and not p.is_symlink() and p.is_file()]


@router.post('/packages')
async def create_package(request: BundleRequest, response: Response):
    if not enabled():
        raise HTTPException(503, 'Создание полного комплекта доступно на сервере с установленным системным помощником')
    async with bundle_lock:
        code, output, _ = await call('migration-export', password=request.password.get_secret_value(), timeout=130)
        if code: raise operation_error(output,503,'MIG_SOURCE_CONFIG')
        data = json.loads(output)
        response.headers['Cache-Control'] = 'no-store'
        return data


def bundle_file(name):
    if not re.fullmatch(r'[0-9a-f]{32}\.mdmove', name): raise HTTPException(404, 'Комплект не найден')
    path = BUNDLE_DIR/name
    if path.is_symlink() or not path.is_file(): raise HTTPException(404, 'Комплект не найден')
    return path


@router.get('/packages/{name}')
async def download_package(name: str):
    return FileResponse(bundle_file(name), filename=name, media_type='application/octet-stream', headers={'Cache-Control':'no-store'})


@router.delete('/packages/{name}', status_code=204)
async def delete_package(name: str):
    bundle_file(name).unlink()


@router.get('/source')
async def source(response: Response, db: AsyncSession = Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    report=await source_report(db)
    if enabled():
        try:
            code,output,_=await call('migration-status',timeout=10)
            status=json.loads(output) if code==0 else {}
            if status.get('target_ready') and status.get('phase') in {'prepared','awaiting_activation','activated'}:
                for check in report['checks']:
                    if check['key']=='target':check.update(status='ok',message='Автоматическая проверка нового VPS пройдена. Внешнюю проверку клиентских подключений выполняет пользователь.')
        except (ValueError,TypeError,RuntimeError):pass
    return report

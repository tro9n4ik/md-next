"""Применение системной политики AWG после успешного запуска Xray."""
from pathlib import Path
from app.services.shell import run_cmd


async def sync_awg_routing(config_path: str) -> None:
    helper = Path(__file__).resolve().parents[3] / 'scripts' / 'awg-routing.py'
    code, _, error = await run_cmd('python3', str(helper), '--config', config_path, timeout=45)
    if code:
        raise RuntimeError('Не удалось применить маршрут AmneziaWG: ' + error[-400:])

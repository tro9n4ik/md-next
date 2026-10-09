"""Finite root helper protocol; enabled only after server migration."""
import asyncio
import json
import os


def enabled():
    return os.getenv('MDNEXT_PRIVILEGED_HELPER') == '1'


async def call(operation, *, timeout=100, **fields):
    process = await asyncio.create_subprocess_exec('/usr/bin/sudo', '-n', '/usr/local/lib/md-next/privileged-helper.py',
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        output, _ = await asyncio.wait_for(process.communicate(json.dumps({'operation': operation, **fields}).encode()), timeout)
    except asyncio.TimeoutError:
        process.kill(); await process.communicate()
        raise RuntimeError('Привилегированная операция превысила время ожидания') from None
    try:
        result = json.loads(output)
        if not isinstance(result, list) or len(result) != 3: raise ValueError()
        return tuple(result)
    except (ValueError, TypeError):
        raise RuntimeError('Некорректный ответ системного помощника') from None

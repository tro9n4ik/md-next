"""Проверки загрузки ключа WARP без сети и установки пакетов."""
import io
import urllib.error

import pytest

from test_migration_bundle import load_script


def test_repository_key_request_has_product_user_agent(monkeypatch):
    runtime=load_script('warp-uplink-runtime.py')
    def respond(request,timeout):
        assert request.full_url=='https://pkg.cloudflareclient.com/pubkey.gpg'
        assert request.get_header('User-agent')=='MD-Next-WARP/1.0'
        assert timeout==20
        return io.BytesIO(b'synthetic-key')
    monkeypatch.setattr(runtime.urllib.request,'urlopen',respond)
    assert runtime.download_repository_key()==b'synthetic-key'


def test_repository_http_error_is_safe_and_specific(monkeypatch):
    runtime=load_script('warp-uplink-runtime.py')
    def denied(*a,**kw):raise urllib.error.HTTPError('https://example.invalid/?secret=hidden',403,'hidden',{},None)
    monkeypatch.setattr(runtime.urllib.request,'urlopen',denied)
    with pytest.raises(ValueError,match='HTTP 403') as error:runtime.download_repository_key()
    assert 'hidden' not in str(error.value)


def test_repository_key_size_is_limited(monkeypatch):
    runtime=load_script('warp-uplink-runtime.py')
    monkeypatch.setattr(runtime.urllib.request,'urlopen',lambda *a,**kw:io.BytesIO(b'x'*(1024*1024+1)))
    with pytest.raises(ValueError):runtime.download_repository_key()

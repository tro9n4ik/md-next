import json
from pathlib import Path
import re
import ssl
import socket
import sys

import pytest
from pydantic import ValidationError

from app.api.nodes import NodeRegister
from app.services.node_transport import node_outbound, validate_reality_domain

ROOT = Path(__file__).resolve().parents[2]
ROW = {"id": 1, "host": "192.0.2.1", "port": 443, "protocol": "vless",
       "secret": "00000000-0000-4000-8000-000000000001", "public_key": "A" * 43}


@pytest.mark.parametrize('domain', ['panel.first.example', 'panel.second.example'])
def test_each_node_uses_its_own_panel_domain(domain):
    result = node_outbound({**ROW, 'reality_server_name': domain})
    assert result['streamSettings']['realitySettings']['serverName'] == domain
    assert node_outbound(ROW)['streamSettings']['realitySettings']['serverName'] == 'www.cloudflare.com'


@pytest.mark.parametrize('bad', ['', 'https://panel.example', 'panel.example:443', '127.0.0.1', 'panel.example/path', 'panel.example\n', '*.example.com'])
def test_invalid_domains_fail_closed(bad):
    with pytest.raises(ValueError):
        validate_reality_domain(bad)
    with pytest.raises(ValidationError):
        NodeRegister(token='synthetic', host=ROW['host'], port=443, protocol='vless',
                     public_key=ROW['public_key'], identity=ROW['secret'], reality_server_name=bad)


@pytest.mark.parametrize('domain', ['panel.first.example', 'panel.second.example'])
def test_installer_checks_certificate_for_its_panel(monkeypatch, capsys, domain):
    script = (ROOT / 'scripts/join-node.sh').read_text(encoding='utf-8')
    source = script.split('NODE_SERVER_NAME=$(python3 - "$PANEL_URL" <<\'PY\'\n', 1)[1].split('\nPY', 1)[0]
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def selected_alpn_protocol(self): return 'h2'
    class Context:
        def set_alpn_protocols(self, names): assert names == ['h2']
        def wrap_socket(self, connection, *, server_hostname):
            assert server_hostname == domain
            return Connection()
    monkeypatch.setattr(sys, 'argv', ['-', 'https://' + domain])
    monkeypatch.setattr(ssl, 'create_default_context', Context)
    def connect(address, **kwargs):
        assert address == (domain, 443)
        return Connection()
    monkeypatch.setattr(socket, 'create_connection', connect)
    exec(compile(source, 'installer-domain', 'exec'), {})
    assert capsys.readouterr().out.strip() == domain
    def reject(*args, **kwargs): raise ssl.SSLCertVerificationError('invalid certificate')
    monkeypatch.setattr(Context, 'wrap_socket', reject)
    with pytest.raises(SystemExit, match='TLS'):
        exec(compile(source, 'installer-domain', 'exec'), {})


def test_installed_xray_target_matches_registered_domain(tmp_path, monkeypatch):
    script = (ROOT / 'scripts/join-node.sh').read_text(encoding='utf-8')
    source = re.search(r'if ! python3 - "\$temporary" <<\'PY\'\n(.*?)\nPY', script, re.S).group(1)
    destination = tmp_path / 'config.json'
    monkeypatch.setattr(sys, 'argv', ['-', str(destination)])
    for key, value in {'NODE_PORT':'443','NODE_SECRET':ROW['secret'], 'NODE_PRIVATE_KEY':'synthetic',
                       'NODE_SHORT_ID':'0123456789abcdef', 'NODE_SERVER_NAME':'panel.owned.example'}.items():
        monkeypatch.setenv(key, value)
    exec(compile(source, 'installer-config', 'exec'), {})
    reality = json.loads(destination.read_text())['inbounds'][0]['streamSettings']['realitySettings']
    assert reality['target'] == 'panel.owned.example:443'
    assert reality['serverNames'] == ['panel.owned.example']


@pytest.mark.asyncio
async def test_registration_rejects_another_panels_domain(monkeypatch):
    from unittest.mock import AsyncMock
    from fastapi import HTTPException
    from app.api.nodes import _register_node
    monkeypatch.setenv('PANEL_PUBLIC_URL', 'https://panel.first.example')
    data = NodeRegister(token='synthetic', host=ROW['host'], port=443, protocol='vless',
                        public_key=ROW['public_key'], identity=ROW['secret'], reality_server_name='panel.second.example')
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await _register_node(data, db)
    assert exc.value.status_code == 400
    db.execute.assert_not_called()

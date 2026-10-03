import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from app.services.xray import XrayService

spec = importlib.util.spec_from_file_location('awg_routes', Path(__file__).resolve().parents[2]/'scripts/awg-routing.py')
routes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(routes)


def test_awg_tun_shares_node_and_warp_rules_without_system_default_route():
    node = SimpleNamespace(id=1, host='203.0.113.2', port=443, protocol='trojan', secret='test', is_enabled=True)
    config = json.loads(XrayService.generate_config([], 'private', active_node=node, server_name='test.example', profile_options={
        'awg_routing': True, 'warp_usage': 'rules', 'enabled': {'awg'},
        'routing_rules': [{'type': 'field', 'domain': ['domain:google.com'], 'outboundTag': 'warp'}],
    }))
    inbound = next(i for i in config['inbounds'] if i.get('tag') == 'awg-in')
    assert inbound['settings'] == {'name': 'mdawg', 'mtu': 1400}
    assert inbound['sniffing']['routeOnly'] is True
    assert config['outbounds'][0]['tag'] == 'node-1'
    assert config['routing']['rules'][-1]['outboundTag'] == 'node-1'
    assert any(r.get('outboundTag') == 'warp' for r in config['routing']['rules'])


def test_policy_applies_only_to_awg_and_keeps_blackhole(tmp_path):
    config = tmp_path/'xray.json'
    config.write_text(json.dumps({'inbounds':[{'protocol':'tun','tag':'awg-in','settings':{'name':'mdawg'}}]}))
    calls = []
    def fake(*args, check=True):
        calls.append(args)
        if args[:4] == ('ip','-j','rule','show'): return SimpleNamespace(returncode=0, stdout='[]')
        if args[:4] == ('ip','-j','route','show'): return SimpleNamespace(returncode=0, stdout='[]')
        if args[:4] == ('ip','-j','-4','address'): return SimpleNamespace(returncode=0, stdout='[{"addr_info":[{"local":"10.8.0.1","prefixlen":24}]}]')
        return SimpleNamespace(returncode=1 if '-C' in args or '-S' in args else 0, stdout='')
    with patch.object(routes, 'STATE', tmp_path/'owned'), patch.object(routes, 'run', side_effect=fake):
        routes.apply(config)
    assert ('ip','rule','add','priority','10086','iif','awg0','lookup','10086') in calls
    assert ('ip','route','replace','blackhole','default','metric','32760','table','10086') in calls
    assert ('ip','route','replace','10.8.0.0/24','dev','awg0','table','10086') in calls
    assert all('table' in c for c in calls if c[:3] == ('ip','route','replace'))
    assert any(c[-2:] == ('-j','REJECT') for c in calls)


def test_conflicting_policy_is_rejected_before_mutation():
    with patch.object(routes, 'run', return_value=SimpleNamespace(stdout='[{"priority":10086,"iif":"ens3","table":10086}]')) as run:
        with pytest.raises(RuntimeError, match='занят'): routes.validate_ownership()
    assert run.call_count == 1


@pytest.mark.asyncio
async def test_awg_policy_failure_restores_previous_xray_config(tmp_path):
    target = tmp_path/'xray.json'
    target.write_text('{"previous":true}')
    with patch.dict('os.environ', {'XRAY_PRIVATE_KEY':'private','XRAY_SERVER_NAME':'example.com'}), \
         patch('app.services.xray.run_cmd', side_effect=[(0,'',''),(0,'',''),(0,'active',''),(0,'','')]), \
         patch('app.services.xray.sync_awg_routing', side_effect=RuntimeError('route failed')):
        ok, _ = await XrayService.apply_config([], config_path=str(target), profile_options={'awg_routing': True})
    assert not ok
    assert json.loads(target.read_text()) == {'previous':True}

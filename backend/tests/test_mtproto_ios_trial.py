import importlib.util
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

path = Path(__file__).resolve().parents[2] / 'experimental/mtproto-ios/render_trial.py'
spec = importlib.util.spec_from_file_location('mtproto_ios_trial', path)
trial = importlib.util.module_from_spec(spec); spec.loader.exec_module(trial)


def payload():
    return {'port':8445,'domain':'example.com','users':{'p1':'11'*16,'p2':'22'*16}}


@pytest.mark.parametrize('host',['example.com','192.0.2.1'])
def test_dd_link_uses_exact_key_without_domain_byte(host):
    text,link = trial.render(payload(),'p1',host)
    query = parse_qs(urlparse(link).query)
    assert query == {'server':[host],'port':['8448'],'secret':['dd'+'11'*16]}
    assert len(bytes.fromhex(query['secret'][0])) == 17
    assert '[secured]\nenabled = true' in text
    assert 'api-bind-to = "127.0.0.1:10911"' in text
    assert 'p2 =' not in text
    assert 'p1 = "ee'+'11'*16+'example.com'.encode().hex()+'"' in text
    assert 'socks5://127.0.0.1:10909' in text


@pytest.mark.parametrize('changes',[{'port':8445},{'stats_port':8448},{'port':443},{'stats_port':70000}])
def test_trial_rejects_conflicting_or_invalid_ports(changes):
    with pytest.raises(ValueError):trial.render(payload(),'p1','example.com',**changes)


def test_unknown_profile_and_malformed_key_are_rejected():
    with pytest.raises(ValueError):trial.render(payload(),'p99','example.com')
    data=payload();data['users']['p1']+='6'
    with pytest.raises(ValueError):trial.render(data,'p1','example.com')

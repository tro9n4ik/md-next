import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location('fingerprint_matrix', Path(__file__).parents[2] / 'scripts/fp_matrix.py')
matrix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matrix)
PROFILE = 'vless://11111111-1111-4111-8111-111111111111@example.com:443?security=reality&type=tcp&sni=example.com&pbk=public-key&flow=xtls-rprx-vision'


def test_matrix_changes_only_fingerprint():
    firefox = matrix.outbound(PROFILE, 'firefox')
    chrome = matrix.outbound(PROFILE, 'chrome')
    chrome['streamSettings']['realitySettings']['fingerprint'] = 'firefox'
    assert firefox == chrome
    assert firefox['streamSettings']['realitySettings']['password'] == 'public-key'


@pytest.mark.parametrize('profile', [PROFILE.replace('type=tcp', 'type=xhttp'), PROFILE.replace('security=reality', 'security=tls'), PROFILE.replace('&sni=example.com', ''), PROFILE.replace('11111111-1111-4111-8111-111111111111', 'invalid')])
def test_matrix_rejects_incomparable_or_incomplete_profiles(profile):
    with pytest.raises(ValueError):
        matrix.outbound(profile, 'firefox')

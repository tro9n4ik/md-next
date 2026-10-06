"""Uninstallation must not remove a foreign routing table or unowned policy."""
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from test_awg_routing import routes


def test_uninstall_without_marker_does_not_touch_network(tmp_path):
    with patch.object(routes, 'STATE', tmp_path / 'absent'), patch.object(routes, 'run') as run:
        routes.remove_owned()
    run.assert_not_called()


def test_uninstall_preserves_foreign_policy_and_marker(tmp_path):
    marker = tmp_path / 'owned'
    marker.write_text('MD-Next AWG routing v1\n')
    foreign = SimpleNamespace(returncode=0, stdout='[{"priority":10086,"iif":"ens3","table":10086}]')
    with patch.object(routes, 'STATE', marker), patch.object(routes, 'run', return_value=foreign) as run:
        with pytest.raises(RuntimeError, match='занят'):
            routes.remove_owned()
    assert marker.exists()
    assert run.call_count == 1


def test_uninstall_removes_owned_rules_and_retains_other_tables(tmp_path):
    marker = tmp_path / 'owned'
    marker.write_text('MD-Next AWG routing v1\n')
    def fake(*args, **kwargs):
        if args[:4] == ('ip', '-j', 'rule', 'show'):
            return SimpleNamespace(returncode=0, stdout='[{"priority":10086,"iif":"awg0","table":10086},{"priority":99,"table":99}]')
        if args[:4] == ('ip', '-j', 'route', 'show'):
            return SimpleNamespace(returncode=0, stdout='[{"dev":"mdawg"}]')
        return SimpleNamespace(returncode=0, stdout='')
    with patch.object(routes, 'STATE', marker), patch.object(routes, 'run', side_effect=fake) as run:
        routes.remove_owned()
    assert not marker.exists()
    calls = [call.args for call in run.call_args_list]
    assert ('ip','rule','del','priority','10086','iif','awg0','lookup','10086') in calls
    assert ('ip','route','flush','table','10086') in calls
    assert all('99' not in args for args in calls)

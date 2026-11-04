import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("network_mtu", Path(__file__).parents[2] / "scripts/network-mtu.py")
mtu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mtu)


def test_mss_caps_include_both_directions_and_families():
    source = mtu.nft_script(False)
    assert source.count("maxseg size set 1340") == 2
    assert source.count("maxseg size set 1320") == 2
    assert "maxseg size > 1340" in source
    assert "maxseg size > 1320" in source
    assert "iifname" in source and "oifname" in source
    assert len(list(mtu.rules())) == 4


def test_foreign_nft_table_is_rejected(monkeypatch):
    monkeypatch.setattr(mtu, "command", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout='{"nftables":[{"table":{"name":"mdnext_mss","comment":"foreign"}}]}'))
    with pytest.raises(RuntimeError):
        mtu.owned_table()


def test_foreign_table_blocks_apply_before_interface_changes(monkeypatch):
    calls = []
    monkeypatch.setattr(mtu.shutil, "which", lambda name: name)
    def command(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout='{"nftables":[{"table":{"comment":"foreign"}}]}')
    monkeypatch.setattr(mtu, "command", command)
    with pytest.raises(RuntimeError):
        mtu.apply()
    assert all(args[0] != "ip" for args in calls)

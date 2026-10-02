from app.services.net_utils import get_primary_interface

def test_get_primary_interface_filters_virtual(monkeypatch):
    monkeypatch.delenv("NET_INTERFACE", raising=False)

    fake_addrs = {
        "lo": [],
        "docker0": [],
        "veth123": [],
        "br-456": [],
        "virbr0": [],
        "tun0": [],
        "wg0": [],
        "awg0": [],
        "eth1": []
    }

    import psutil
    monkeypatch.setattr(psutil, "net_if_addrs", lambda: fake_addrs)
    monkeypatch.setattr(psutil, "net_if_stats", lambda: {"eth1": type("S", (), {"isup": True})()})
    monkeypatch.setattr("os.path.exists", lambda path: False)

    assert get_primary_interface() == "eth1"


def test_get_primary_interface_skips_down_real_interface(monkeypatch):
    monkeypatch.delenv("NET_INTERFACE", raising=False)

    import psutil
    monkeypatch.setattr(psutil, "net_if_addrs", lambda: {"eth1": [], "eth2": []})
    monkeypatch.setattr(psutil, "net_if_stats", lambda: {
        "eth1": type("S", (), {"isup": False})(),
        "eth2": type("S", (), {"isup": True})(),
    })
    monkeypatch.setattr("os.path.exists", lambda path: False)

    assert get_primary_interface() == "eth2"

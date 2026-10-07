from app.services import client_activity as activity


def test_unknown_offline_and_recent_protocols(monkeypatch):
    monkeypatch.setattr(activity, '_sources', {})
    monkeypatch.setattr(activity, '_seen', {})
    clock = [1000.0]
    monkeypatch.setattr(activity.time, 'monotonic', lambda: clock[0])
    kinds = ['vless_reality_tcp', 'awg']
    assert activity.client_activity(1, kinds, True)['connection_status'] == 'unknown'
    activity.source_checked('xray')
    activity.source_checked('awg')
    assert activity.client_activity(1, kinds, True)['connection_status'] == 'offline'
    activity.record_activity(1, kinds[0], 5, 0)
    assert activity.client_activity(1, kinds, True)['connected_protocols'] == [kinds[0]]
    assert activity.client_activity(2, kinds, True)['connection_status'] == 'offline'
    assert activity.client_activity(1, kinds, False)['connected_protocols'] == []
    clock[0] += 151
    assert activity.client_activity(1, kinds, True)['connection_status'] == 'unknown'
    activity.source_checked('xray')
    activity.source_checked('awg')
    assert activity.client_activity(1, kinds, True)['connection_status'] == 'online'
    clock[0] += 30
    assert activity.client_activity(1, kinds, True)['connection_status'] == 'offline'

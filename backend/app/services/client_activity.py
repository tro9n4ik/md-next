"""Recent profile activity; never confuse missing telemetry with an offline user."""
import time

ACTIVITY_WINDOW = 180
TELEMETRY_WINDOW = 150
_seen: dict[tuple[int, str], float] = {}
_sources: dict[str, float] = {}


def source_checked(source: str) -> None:
    now = time.monotonic()
    _sources[source] = now
    for key, seen in list(_seen.items()):
        if now - seen > ACTIVITY_WINDOW:
            del _seen[key]


def record_activity(client_id: int, kind: str, upload: int, download: int) -> None:
    if upload > 0 or download > 0:
        _seen[(client_id, kind)] = time.monotonic()


def client_activity(client_id: int, kinds: list[str], allowed: bool) -> dict:
    now = time.monotonic()
    connected = [kind for kind in kinds if allowed and
                 now - _sources.get('awg' if kind == 'awg' else 'xray', float('-inf')) <= TELEMETRY_WINDOW and
                 now - _seen.get((client_id, kind), float('-inf')) <= ACTIVITY_WINDOW]
    known = all(now - _sources.get('awg' if kind == 'awg' else 'xray', float('-inf')) <= TELEMETRY_WINDOW for kind in kinds)
    return {'connection_status': 'online' if connected else 'offline' if not allowed or known else 'unknown',
            'connected_protocols': connected, 'activity_window_seconds': ACTIVITY_WINDOW}

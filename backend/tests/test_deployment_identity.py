import httpx
import asyncio
import pytest
from app.services import deployment_identity as identity
from app.services import panel_updates as updates

HEAD = 'a' * 40
OLD = 'b' * 40


@pytest.fixture
def installation(tmp_path, monkeypatch):
    for name in ('backend/app/main.py', 'backend/VERSION', 'backend/requirements.txt', 'frontend/src/App.tsx', 'frontend/package.json', 'frontend/package-lock.json', 'scripts/install.sh'):
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(name.encode())
    monkeypatch.setattr(updates, 'APP_ROOT', tmp_path)
    identity._cache.clear()
    return tmp_path


def tree(files):
    return {'tree': [{'path': name, 'type': 'blob', 'mode': '100644', 'sha': digest} for name, digest in files.items()]}


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['match', 'modified', 'extra', 'incomplete', 'truncated', 'symlink'])
async def test_recovers_only_complete_matching_runtime(installation, case):
    files = identity.local_tree(installation)
    if case == 'modified':
        (installation / 'backend/app/main.py').write_text('customized code')
    if case == 'extra':
        (installation / 'backend/app/extra.py').write_text('extra code')
    if case == 'incomplete':
        (installation / 'frontend/src/App.tsx').unlink()
    remote = tree(files)
    if case == 'truncated':
        remote['truncated'] = True
    if case == 'symlink':
        remote['tree'][0]['mode'] = '120000'
    def respond(request):
        return httpx.Response(200, json=[{'sha': HEAD}] if request.url.path.endswith('/commits') else remote)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await identity.identify_installation(client, 'owner/project', installation, HEAD, {})
    assert result == (HEAD if case == 'match' else '')
    assert not (installation / 'DEPLOYED_COMMIT').exists(), 'API must not write root-owned metadata'


@pytest.mark.asyncio
async def test_recovery_is_cached_but_changed_files_invalidate_cache(installation):
    remote = tree(identity.local_tree(installation))
    requests = []
    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(200, json=[{'sha': HEAD}] if request.url.path.endswith('/commits') else remote)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        assert await identity.identify_installation(client, 'owner/project', installation, HEAD, {}) == HEAD
        count = len(requests)
        assert await identity.identify_installation(client, 'owner/project', installation, HEAD, {}) == HEAD
        assert len(requests) == count
        (installation / 'backend/app/main.py').write_text('changed')
        assert await identity.identify_installation(client, 'owner/project', installation, HEAD, {}) == ''
        assert len(requests) > count


@pytest.mark.asyncio
async def test_old_archive_installation_can_receive_verified_newer_update(installation, monkeypatch):
    old_tree = identity.local_tree(installation)
    new_tree = {**old_tree, 'backend/app/main.py': 'c' * 40}
    requests = []
    def respond(request):
        requests.append(request.url.path)
        if request.url.path.endswith('/commits/main'):
            return httpx.Response(200, json={'sha': HEAD, 'commit': {'verification': {'verified': True}}})
        if request.url.path.endswith('/commits'):
            return httpx.Response(200, json=[{'sha': HEAD}, {'sha': OLD}])
        if '/git/trees/' in request.url.path:
            return httpx.Response(200, json=tree(old_tree if request.url.path.endswith(OLD) else new_tree))
        if '/compare/' in request.url.path:
            assert f'{OLD}...{HEAD}' in request.url.path
            return httpx.Response(200, json={'status': 'ahead'})
        return httpx.Response(200, json={'workflow_runs': [{'path': path, 'conclusion': 'success'} for path in ('.github/workflows/ci.yml', '.github/workflows/secrets.yml')]})
    factory = httpx.AsyncClient
    monkeypatch.setattr(updates.httpx, 'AsyncClient', lambda **kwargs: factory(transport=httpx.MockTransport(respond), **kwargs))
    result = await updates.check_update()
    assert result['available'] and result['ready'] and result['signature_verified']
    assert result['installed']['commit'] == OLD
    assert result['installed']['commit_source'] == 'files'
    assert result['identity_notice']


@pytest.mark.asyncio
async def test_unidentified_installation_stays_blocked(installation, monkeypatch):
    factory = httpx.AsyncClient
    def respond(request):
        if request.url.path.endswith('/commits/main'):
            return httpx.Response(200, json={'sha': HEAD, 'commit': {'verification': {'verified': True}}})
        if request.url.path.endswith('/commits'):
            return httpx.Response(200, json=[])
        if '/git/trees/' in request.url.path:
            return httpx.Response(200, json={'tree': []})
        return httpx.Response(200, json={'workflow_runs': []})
    monkeypatch.setattr(updates.httpx, 'AsyncClient', lambda **kwargs: factory(transport=httpx.MockTransport(respond), **kwargs))
    result = await updates.check_update()
    assert not result['available']
    assert result['relation'] == 'unknown'
    assert 'отсутствует' in result['identity_notice']


@pytest.mark.asyncio
async def test_slow_identity_lookup_does_not_hang_or_enable_update(installation, monkeypatch):
    async def slow(*args):
        await asyncio.Event().wait()
    monkeypatch.setattr(identity, 'identify_installation', slow)
    monkeypatch.setattr(updates, 'IDENTITY_TIMEOUT', 0.01)
    factory = httpx.AsyncClient
    def respond(request):
        if request.url.path.endswith('/commits/main'):
            return httpx.Response(200, json={'sha': HEAD, 'commit': {'verification': {'verified': True}}})
        return httpx.Response(200, json={'workflow_runs': []})
    monkeypatch.setattr(updates.httpx, 'AsyncClient', lambda **kwargs: factory(transport=httpx.MockTransport(respond), **kwargs))
    result = await updates.check_update()
    assert not result['available']
    assert result['relation'] == 'unknown'
    assert 'отведённое время' in result['identity_notice']

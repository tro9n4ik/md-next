import json
import httpx
import pytest
from app.main import app
from app.services import placeholder as site


@pytest.fixture(autouse=True)
def directories(tmp_path, monkeypatch):
    monkeypatch.setenv('MD_PLACEHOLDER_SITE_DIR', str(tmp_path / 'public'))
    monkeypatch.setenv('MD_PLACEHOLDER_STATE_DIR', str(tmp_path / 'private'))


def test_upload_restore_and_bounded_private_history():
    original = site.generate()
    site.replace(original, 'index.html', 'generated')
    custom = b'<!doctype html><html><body>My page</body></html>'
    site.replace(custom, 'my-page.html')
    assert site.content().encode() == custom
    assert site.status()['mode'] == 'custom'
    site.restore()
    assert site.content().encode() == original
    assert site.status()['mode'] == 'generated'
    for _ in range(8):
        site.replace(custom, 'my-page.html')
    assert len(list(site.paths()[1].glob('revision-*.json'))) == 5
    assert not list(site.paths()[0].glob('revision-*'))


def test_initialize_varies_between_installations_and_preserves_custom():
    assert len({site.generate() for _ in range(12)}) == 12
    site.initialize()
    custom = '<html><body>Мой сайт</body></html>'.encode()
    site.replace(custom, 'own.html')
    site.initialize()
    assert site.content().encode() == custom


@pytest.mark.parametrize('data,name', [(b'', 'x.html'), (b'<html></html>', '../x.html'), (b'<html></html>', 'x.exe'), (b'\xff<html></html>', 'x.html'), (b'<html>\x00</html>', 'x.html'), (b'plain text', 'x.html'), (b'a' * (site.MAX_BYTES + 1), 'x.html')], ids=['empty', 'path', 'extension', 'encoding', 'null', 'incomplete', 'large'])
def test_invalid_upload_keeps_current_page(data, name):
    original = site.generate()
    site.replace(original, 'index.html')
    with pytest.raises(ValueError):
        site.replace(data, name)
    assert site.content().encode() == original


def test_failed_metadata_write_rolls_back_page(monkeypatch):
    original = site.generate()
    site.replace(original, 'index.html', 'generated')
    old_state = site.status()
    atomic = site._atomic
    def fail(path, data, mode):
        if path.name == 'state.json':
            raise OSError('disk full')
        atomic(path, data, mode)
    monkeypatch.setattr(site, '_atomic', fail)
    with pytest.raises(OSError):
        site.replace(b'<html>changed</html>', 'custom.html')
    assert site.content().encode() == original
    assert site.status()['updated_at'] == old_state['updated_at']


@pytest.mark.asyncio
async def test_api_requires_auth_and_validates_upload(auth_headers):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        for method, path in [('GET', ''), ('GET', '/content'), ('PUT', ''), ('POST', '/generate'), ('POST', '/restore')]:
            assert (await client.request(method, '/api/v1/settings/placeholder' + path)).status_code in (401, 403)
        assert (await client.post('/api/v1/settings/placeholder/restore', headers=auth_headers)).status_code == 400
        page = '<html><body>Пример</body></html>'.encode()
        response = await client.put('/api/v1/settings/placeholder?filename=own.html', content=page, headers=auth_headers)
        assert response.status_code == 200
        assert response.json()['mode'] == 'custom'
        response = await client.get('/api/v1/settings/placeholder/content', headers=auth_headers)
        assert response.content == page
        assert response.headers['content-type'].startswith('text/plain')
        assert response.headers['x-content-type-options'] == 'nosniff'
        assert (await client.put('/api/v1/settings/placeholder', content=b'x' * (site.MAX_BYTES + 1), headers=auth_headers)).status_code == 413
        assert site.content().encode() == page

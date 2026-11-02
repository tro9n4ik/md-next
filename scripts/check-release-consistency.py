"""Fail CI when release documentation or screenshot metadata is stale."""
import hashlib
import json
import re
import struct
from pathlib import Path

root = Path(__file__).resolve().parents[1]


def jpeg_dimensions(data):
    offset = 2
    while offset < len(data):
        assert data[offset] == 0xFF, 'Invalid JPEG marker'
        while data[offset] == 0xFF:
            offset += 1
        marker = data[offset]
        offset += 1
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        length = struct.unpack_from('>H', data, offset)[0]
        if marker in (0xC0, 0xC1, 0xC2):
            height, width = struct.unpack_from('>HH', data, offset + 3)
            return width, height
        assert marker != 0xDA, 'Missing JPEG dimensions'
        offset += length
    raise AssertionError('Missing JPEG dimensions')

version = (root / 'backend/VERSION').read_text().strip()
assert re.fullmatch(r'\d+\.\d+\.\d+', version), 'Invalid release version'
readme = (root / 'README.md').read_text(encoding='utf-8')
assert f'Текущая версия: **{version}**' in readme, 'README version is stale'
changelog = (root / 'CHANGELOG.md').read_text(encoding='utf-8')
assert re.search(r'^## (\d+\.\d+\.\d+)', changelog, re.M).group(1) == version, 'CHANGELOG version is stale'
assets = root / 'docs/assets'
manifest = json.loads((assets / 'screenshots.json').read_text())
assert manifest['version'] == version, 'Regenerate screenshots for this release'
expected = {
    'dashboard-demo.jpg', 'clients-demo.jpg', 'telegram-demo.jpg',
    'nodes-demo.jpg', 'warp-demo.jpg', 'protocols-demo.jpg',
    'subadmins-demo.jpg', 'dashboard-dark-demo.jpg',
    'client-create-demo.jpg', 'settings-demo.jpg', 'wiki-demo.jpg',
}
assert set(manifest['files']) == expected, 'Missing screenshot metadata'
assert manifest.get('demo') is True, 'Screenshots must use demo data'
assert set(manifest.get('captures', {})) == expected, 'Missing capture provenance'
for name, digest in manifest['files'].items():
    assert manifest['captures'][name]['viewport'] == {'width': 1920, 'height': 1080}, f'Invalid screenshot viewport: {name}'
    assert (assets / name).read_bytes().startswith(b'\xff\xd8'), f'Not a JPEG: {name}'
    assert jpeg_dimensions((assets / name).read_bytes()) == (1920, 1080), f'Invalid image size: {name}'
    assert hashlib.sha256((assets / name).read_bytes()).hexdigest() == digest, f'Screenshot changed: {name}'
    assert f'docs/assets/{name}' in readme, f'Screenshot missing from README: {name}'
print(f'Release {version}: documentation and screenshot manifest agree')

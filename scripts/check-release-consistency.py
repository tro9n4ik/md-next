"""Fail CI when release documentation or screenshot metadata is stale."""
import hashlib
import json
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
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
}
assert set(manifest['files']) == expected, 'Missing screenshot metadata'
assert manifest.get('demo') is True, 'Screenshots must use demo data'
assert set(manifest.get('captures', {})) == expected, 'Missing capture provenance'
for name, digest in manifest['files'].items():
    assert (assets / name).read_bytes().startswith(b'\xff\xd8'), f'Not a JPEG: {name}'
    assert hashlib.sha256((assets / name).read_bytes()).hexdigest() == digest, f'Screenshot changed: {name}'
    assert f'docs/assets/{name}' in readme, f'Screenshot missing from README: {name}'
print(f'Release {version}: documentation and screenshot manifest agree')

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
assert set(manifest['files']) == {'dashboard-demo.jpg', 'clients-demo.jpg', 'telegram-demo.jpg'}, 'Missing screenshot metadata'
for name, digest in manifest['files'].items():
    assert hashlib.sha256((assets / name).read_bytes()).hexdigest() == digest, f'Screenshot changed: {name}'
    assert f'docs/assets/{name}' in readme, f'Screenshot missing from README: {name}'
print(f'Release {version}: documentation and screenshot manifest agree')

import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest


@pytest.mark.skipif(os.name == 'nt' or not shutil.which('bash'), reason='Linux node installer check')
@pytest.mark.parametrize('reject', ['0', '1'])
def test_node_validates_json_tempfile_before_replacing_live_config(tmp_path, reject):
    source = (Path(__file__).resolve().parents[2] / 'scripts/join-node.sh').read_text()
    function = 'configure_xray_node() {' + source.split('configure_xray_node() {', 1)[1].split('\nverify_and_start_xray() {', 1)[0]
    config_dir = tmp_path / 'xray'
    config_dir.mkdir()
    backup_dir = tmp_path / 'backups'
    previous = '{"previous":true}'
    (config_dir / 'config.json').write_text(previous)
    function = function.replace('/usr/local/etc/xray', str(config_dir)).replace('/var/backups/md-next', str(backup_dir))
    mocks = '''
systemctl() { printf 'root\n'; }
chown() { :; }
xray() {
  if ! python3 - "${@: -1}" <<'CHECK'
import json, sys
assert sys.argv[1].endswith('.json'), 'unsupported config file extension'
with open(sys.argv[1]) as file: config=json.load(file)
assert config['inbounds'][0]['streamSettings']['security']=='reality'
assert config['inbounds'][0]['settings']['clients'][0]['id']=='11111111-1111-4111-8111-111111111111'
CHECK
  then
    return 1
  fi
  [ "$XRAY_REJECT" = '0' ]
}
'''
    env = {**os.environ, 'XRAY_REJECT': reject, 'NODE_PORT': '4443', 'NODE_SECRET': '11111111-1111-4111-8111-111111111111', 'NODE_PRIVATE_KEY': 'synthetic-private-key', 'NODE_SHORT_ID': '0123456789abcdef'}
    result = subprocess.run(['bash', '-c', 'set -e\n' + mocks + function + '\nconfigure_xray_node\n'], env=env, capture_output=True, text=True, timeout=10)
    assert not list(config_dir.glob('md-next-config.*')), 'Temporary configuration must be cleaned up'
    if reject == '1':
        assert result.returncode != 0
        assert (config_dir / 'config.json').read_text() == previous
        assert not backup_dir.exists()
    else:
        assert result.returncode == 0, result.stderr
        assert json.loads((config_dir / 'config.json').read_text())['inbounds'][0]['protocol'] == 'vless'
        assert (config_dir / 'config.json').stat().st_mode & 0o777 == 0o640
        backups = list(backup_dir.glob('xray-node-*.json'))
        assert len(backups) == 1 and backups[0].read_text() == previous

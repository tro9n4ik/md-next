"""Проверка условия установщика без запуска установки и системных служб."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('config,expected', [
    ('stream { include /etc/nginx/stream-enabled/*; }', 'existing'),
    ('stream { include /etc/nginx/stream-enabled/*.conf; }', 'existing'),
    ('stream {\n  include\t/etc/nginx/stream-enabled/*.conf ;\n}', 'existing'),
    ('# stream { include /etc/nginx/stream-enabled/*.conf; }\n', 'missing'),
    ('http { include /etc/nginx/conf.d/*.conf; }', 'missing'),
])
def test_installer_recognizes_existing_stream_include(tmp_path,config,expected):
    shell=shutil.which('sh')
    if not shell:pytest.skip('Требуется shell с sed и grep')
    installer=(Path(__file__).resolve().parents[2]/'scripts/install.sh').read_text(encoding='utf-8')
    condition=next(line.strip() for line in installer.splitlines() if line.lstrip().startswith('if ! ') and '/etc/nginx/nginx.conf' in line and 'stream-enabled' in line)
    path=tmp_path/'nginx.conf';path.write_text(config,encoding='utf-8')
    condition=condition.replace('/etc/nginx/nginx.conf','"$MDNEXT_TEST_NGINX"')
    result=subprocess.run([shell,'-c',condition+'\nprintf missing\nelse\nprintf existing\nfi'],env={**os.environ,'MDNEXT_TEST_NGINX':path.as_posix()},capture_output=True,text=True,check=True)
    assert result.stdout==expected

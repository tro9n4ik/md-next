#!/usr/bin/env bash
set -euo pipefail

[[ ${MDNEXT_DISPOSABLE_VM:-} == 1 && $EUID == 0 && ! -e /opt/md-next && ! -e /etc/systemd/system/md-next-backend.service ]]
base=ac93907814ef71962a47ddc4330b85ec0058d4f2
candidate=a41183d11f58b540346cc818e3107c89b6c6edb9
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT

git init -q "$scratch/base"
git -C "$scratch/base" fetch --depth=1 https://github.com/tro9n4ik/md-next.git "$base"
git -C "$scratch/base" checkout -q --detach FETCH_HEAD
test "$(git -C "$scratch/base" rev-parse HEAD)" = "$base"
if ! MDNEXT_TEST_SELF_SIGNED=1 MDNEXT_PROGRESS=1 bash "$scratch/base/scripts/install.sh" 2>&1 <<'INPUT' | tee "$scratch/install.log" | awk '/^MDNEXT_EVENT / { print; fflush() }'
1
md-next.test
panel.md-next.test
admin@example.com
INPUT
then
  grep -E 'Ошибка при выполнении шага|HTTP Error [0-9]+|curl: \([0-9]+\)' "$scratch/install.log" | tail -12 || true
  exit 1
fi

mss_count() {
  iptables -t mangle -S FORWARD | awk '/MD-Next AWG MSS/ {n++} END {print n+0}'
}
test "$(cat /opt/md-next/DEPLOYED_COMMIT)" = "$base"
test "$(mss_count)" = 0
echo 'Базовая версия 2.5.9 установлена, правил MSS нет.'

/usr/bin/python3 - "$candidate" <<'PY'
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

spec = importlib.util.spec_from_file_location("updater", "/usr/local/lib/md-next/update-panel.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
candidate = sys.argv[1]
assert os.environ.get("MDNEXT_DISPOSABLE_VM") == "1"

class CandidateTest(module.Updater):
    def verify_revision(self, token):
        # Only the disposable test accepts the pinned PR revision.
        assert self.request["commit"] == candidate and not token

    def phase(self, phase, message):
        print("Этап обновления:", phase, flush=True)
        super().phase(phase, message)

    def run(self, args, **options):
        try:
            return super().run(args, **options)
        except subprocess.SubprocessError as error:
            print(f"Ошибка команды {Path(args[0]).name}: {type(error).__name__}", flush=True)
            raise

module.STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
updater = CandidateTest(module.ROOT, module.STATE, {"commit": candidate, "python": "/usr/bin/python3"})
with (module.STATE / "lab-update.log").open("w") as log:
    updater.log = log
    if not updater.execute():
        raise SystemExit(1)
PY

test "$(cat /opt/md-next/DEPLOYED_COMMIT)" = "$candidate"
test "$(cat /opt/md-next/FRONTEND_DEPLOYED_COMMIT)" = "$candidate"
for unit in md-next-backend xray nginx; do
  systemctl is-active --quiet "$unit"
done
test "$(mss_count)" = 2
systemctl restart md-next-backend
test "$(mss_count)" = 2
curl -fsS --retry 8 --retry-connrefused --retry-delay 1 http://127.0.0.1:8000/health >/dev/null
echo 'UPDATE_MSS_OK: правила появились при обновлении и сохранились без дубликатов.'

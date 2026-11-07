#!/usr/bin/env bash
# Verified upstream v3 implementation for kernels without matching DKMS headers.
set -euo pipefail
case "$(uname -m)" in
  x86_64) arch=amd64; checksum=65efbc80fdc1fa2cd2631cad5b481777a68e8725f34e89f50fc6ac714e379b40 ;;
  aarch64) arch=arm64; checksum=a06e99309dbcbdca1a8ccd2282c3ee146e55930886e26b4f1193269587165f49 ;;
  *) echo 'Нет сборки AmneziaWG для этой архитектуры.' >&2; exit 1 ;;
esac
build_dir="$(mktemp -d /tmp/md-next-awg.XXXXXX)"
trap 'rm -rf -- "$build_dir"' EXIT
curl -fSL --retry 3 "https://go.dev/dl/go1.27.3.linux-$arch.tar.gz" -o "$build_dir/go.tar.gz"
printf '%s  %s\n' "$checksum" "$build_dir/go.tar.gz" | sha256sum -c -
tar -xzf "$build_dir/go.tar.gz" -C "$build_dir"
git clone https://github.com/amnezia-vpn/amneziawg-go.git "$build_dir/source"
git -C "$build_dir/source" checkout --detach b5928efb6ca19f0153958460c3d141f04abc5c2e
cd "$build_dir/source"
export GOTOOLCHAIN=local GOCACHE="$build_dir/cache" GOPATH="$build_dir/modules"
"$build_dir/go/bin/go" build -mod=readonly -trimpath -o "$build_dir/amneziawg-go" .
install -m 0755 "$build_dir/amneziawg-go" /usr/local/bin/amneziawg-go

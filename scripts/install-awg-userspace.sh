#!/usr/bin/env bash
# Verified upstream v3 implementation for kernels without matching DKMS headers.
set -euo pipefail
case "$(uname -m)" in
  x86_64) arch=amd64; checksum=63d339f0da5ab53635a56f2490a7984dfe12dfcff22ad749f63edaf590168445 ;;
  aarch64) arch=arm64; checksum=3450b45a3f9ee8568792736a5c5e70a1f2e9b36c35a8f74958c03e51d7d92bec ;;
  *) echo 'Нет сборки AmneziaWG для этой архитектуры.' >&2; exit 1 ;;
esac
build_dir="$(mktemp -d /tmp/md-next-awg.XXXXXX)"
trap 'rm -rf -- "$build_dir"' EXIT
curl -fSL --retry 3 "https://go.dev/dl/go1.27.1.linux-$arch.tar.gz" -o "$build_dir/go.tar.gz"
printf '%s  %s\n' "$checksum" "$build_dir/go.tar.gz" | sha256sum -c -
tar -xzf "$build_dir/go.tar.gz" -C "$build_dir"
git clone https://github.com/amnezia-vpn/amneziawg-go.git "$build_dir/source"
git -C "$build_dir/source" checkout --detach b5928efb6ca19f0153958460c3d141f04abc5c2e
cd "$build_dir/source"
export GOTOOLCHAIN=local GOCACHE="$build_dir/cache" GOPATH="$build_dir/modules"
"$build_dir/go/bin/go" build -mod=readonly -trimpath -o "$build_dir/amneziawg-go" .
install -m 0755 "$build_dir/amneziawg-go" /usr/local/bin/amneziawg-go

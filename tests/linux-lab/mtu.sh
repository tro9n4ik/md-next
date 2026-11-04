#!/usr/bin/env bash
set -euo pipefail

scratch=$(mktemp -d)
client=mdmtu-client
router=mdmtu-router
server=mdmtu-server
namespaces=()
processes=()
cleanup() {
  for pid in "${processes[@]}"; do kill "$pid" 2>/dev/null || true; done
  for pid in "${processes[@]}"; do wait "$pid" 2>/dev/null || true; done
  for ns in "${namespaces[@]}"; do ip netns del "$ns"; done
  rm -rf -- "$scratch"
}
trap cleanup EXIT
for ns in "$client" "$router" "$server"; do
  ip netns add "$ns"
  namespaces+=("$ns")
  ip -n "$ns" link set lo up
done

ip -n "$router" link add awg0 type veth peer name client0 netns "$client"
ip -n "$router" link add mdawg type veth peer name server0 netns "$server"
ip -n "$router" addr add 192.0.2.1/24 dev awg0
ip -n "$client" addr add 192.0.2.2/24 dev client0
ip -n "$router" addr add 198.51.100.1/24 dev mdawg
ip -n "$server" addr add 198.51.100.2/24 dev server0
ip -n "$router" link set awg0 up
ip -n "$router" link set mdawg up
ip -n "$client" link set client0 up
ip -n "$server" link set server0 up
ip -n "$client" route add default via 192.0.2.1
ip -n "$server" route add default via 198.51.100.1
ip -n "$router" route replace 198.51.100.0/24 dev mdawg src 198.51.100.1 mtu 1280
ip -n "$router" route replace 192.0.2.0/24 dev awg0 src 192.0.2.1 mtu 1280
ip netns exec "$router" sysctl -qw net.ipv4.ip_forward=1
for ns in "$client" "$server"; do
  ip netns exec "$ns" sysctl -qw net.ipv4.tcp_mtu_probing=0
done

probe_mtu() {
  ip netns exec "$client" ping -c 1 -W 1 -M do -s 1252 198.51.100.2 >/dev/null
  if ip netns exec "$client" ping -c 1 -W 1 -M do -s 1253 198.51.100.2 >/dev/null; then
    echo 'Пакет с DF превышает заданный MTU, но прошёл' >&2
    exit 1
  fi
  echo "PMTU 1280 ($1)"
}

openssl req -x509 -newkey rsa:2048 -nodes -keyout "$scratch/key" -out "$scratch/cert" \
  -subj /CN=mtu.test -days 1 2>/dev/null
ip netns exec "$server" openssl s_server -quiet -www -accept 198.51.100.2:443 \
  -cert "$scratch/cert" -key "$scratch/key" >"$scratch/tls.log" 2>&1 &
processes+=("$!")
ip netns exec "$server" iperf3 -s -B 198.51.100.2 >"$scratch/iperf.log" 2>&1 &
processes+=("$!")
sleep 1

measure() {
  local label=$1
  if ip netns exec "$client" curl --noproxy '*' -kfSs --max-time 5 \
      https://198.51.100.2/ -o "$scratch/https"; then
    echo "$label HTTPS OK ($(wc -c < "$scratch/https") bytes)"
  else
    echo "$label HTTPS FAILED"
    [[ $label == blocked ]]
  fi
  if timeout 8 ip netns exec "$client" iperf3 -c 198.51.100.2 -t 2 -J >"$scratch/result.json"; then
    python3 - "$label" "$scratch/result.json" <<'PY'
import json
import sys
with open(sys.argv[2]) as stream:
    report = json.load(stream)
rate = report["end"]["sum_received"]["bits_per_second"]
print(f"{sys.argv[1]} TCP {rate / 1e6:.1f} Mbit/s")
assert sys.argv[1] == "blocked" or rate > 0
PY
  else
    echo "$label TCP FAILED"
    [[ $label == blocked ]]
  fi
}

# Observe PMTU in a separate flow before inducing packet loss. Refresh routes
# on both endpoints so that previous discovery cannot mask the failing case.
probe_mtu control
measure control
ip -n "$client" route del default
ip -n "$client" route add default via 192.0.2.1
ip -n "$server" route del default
ip -n "$server" route add default via 198.51.100.1
ip netns exec "$router" iptables -A OUTPUT -p icmp --icmp-type fragmentation-needed -j DROP
measure blocked

for _ in 1 2; do
  for pair in 'awg0 mdawg' 'mdawg awg0'; do
    read -r ingress egress <<< "$pair"
    rule=(-i "$ingress" -o "$egress" -p tcp --tcp-flags SYN,RST SYN -m tcpmss --mss 1241:65535 -j TCPMSS --set-mss 1240)
    ip netns exec "$router" iptables -t mangle -C FORWARD "${rule[@]}" 2>/dev/null ||
      ip netns exec "$router" iptables -t mangle -A FORWARD "${rule[@]}"
  done
done
[[ $(ip netns exec "$router" iptables -t mangle -S FORWARD | grep -c -- '-j TCPMSS') == 2 ]]
measure clamped
probe_mtu clamped
for pair in 'awg0 mdawg' 'mdawg awg0'; do
  read -r ingress egress <<< "$pair"
  ip netns exec "$router" iptables -t mangle -D FORWARD -i "$ingress" -o "$egress" \
    -p tcp --tcp-flags SYN,RST SYN -m tcpmss --mss 1241:65535 -j TCPMSS --set-mss 1240
done
[[ $(ip netns exec "$router" iptables -t mangle -S FORWARD) == '-P FORWARD ACCEPT' ]]
echo 'MSS_RULES_IDEMPOTENT_AND_REMOVED'

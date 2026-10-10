"""Create the three WireGuard interfaces.

wga: tunnel over bearer A (5G). wgb: tunnel over bearer B (satellite), cold on the
vehicle until a controller warms it. wgm: a single tunnel that can roam between
bearers (MOBIKE / WireGuard endpoint roaming), used only by the MOBIKE mode.
"""
import sys
from net import (sh, pub, PORT, UNDERLAY, SERVICE, KEYS, inner_ip)

role = sys.argv[1]
peer = "vehicle" if role == "hq" else "hq"

for n in ("a", "b", "m"):
    dev = f"wg{n}"
    sh(f"ip link del {dev}", check=False)
    sh(f"ip link add {dev} type wireguard")
    sh(f"wg set {dev} private-key {KEYS}/{role}.{n}.key listen-port {PORT[n]}")
    sh(f"ip addr add {inner_ip(n, role)}/24 dev {dev}")
    sh(f"ip link set {dev} up")

sh(f"ip addr add {SERVICE[role]}/32 dev lo", check=False)

if role == "hq":
    # Passive responder: peers always known, endpoints learned from authenticated packets.
    for n in ("a", "b", "m"):
        sh(f"wg set wg{n} peer {pub(peer, n)} allowed-ips {inner_ip(n, peer)}/32,{SERVICE[peer]}/32")
else:
    sh(f"wg set wga peer {pub(peer, 'a')} endpoint {UNDERLAY['hq']['a']}:{PORT['a']} "
       f"allowed-ips {inner_ip('a', peer)}/32,{SERVICE[peer]}/32")
    sh(f"wg set wgm peer {pub(peer, 'm')} endpoint {UNDERLAY['hq']['a']}:{PORT['m']} "
       f"allowed-ips {inner_ip('m', peer)}/32,{SERVICE[peer]}/32")
    # wgb stays cold (no peer) until a controller warms it.
    sh(f"ip route replace {SERVICE[peer]}/32 dev wga src {SERVICE[role]}")
print(f"[{role}] wireguard ready")

"""Vehicle traffic: push-to-talk voice (50 pkt/s, 160 B) and a bodycam video flow."""
import argparse
import socket
import time
from net import SERVICE, VOICE_PORT, VIDEO_PORT

ap = argparse.ArgumentParser()
ap.add_argument("--run", required=True)
ap.add_argument("--duration", type=float, required=True)
ap.add_argument("--kind", choices=["voice", "video", "bulk"], default="voice")
ap.add_argument("--video-mbps", type=float, default=1.5)
a = ap.parse_args()

if a.kind == "bulk":
    # Greedy TCP bulk transfer (like the simulator's bulk source): goodput tracks capacity.
    from net import SERVICE as _S
    t0 = time.time()
    buf = b"b" * 65536
    while time.time() - t0 < a.duration:
        try:
            c = socket.create_connection((_S["hq"], 5002), timeout=2, source_address=(_S["vehicle"], 0))
            c.settimeout(2)
            while time.time() - t0 < a.duration:
                c.sendall(buf)
        except OSError:
            time.sleep(0.5)       # tunnel being rebuilt: reconnect, as an application would
    raise SystemExit(0)

if a.kind == "voice":
    port, size, interval = VOICE_PORT, 160, 0.02
else:
    port, size = VIDEO_PORT, 1200
    interval = size * 8 / (a.video_mbps * 1e6)

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind((SERVICE["vehicle"], 0))
dst = (SERVICE["hq"], port)
t0 = time.time()
seq = 0
nxt = t0
while True:
    now = time.time()
    if now - t0 > a.duration:
        break
    head = f"{a.run}|{seq}|{now:.6f}|".encode()
    try:
        s.sendto(head + b"x" * (size - len(head)), dst)
    except OSError:
        pass  # no route while a tunnel is being rebuilt: the packet is lost, as on a real link
    seq += 1
    nxt += interval
    time.sleep(max(0.0, nxt - time.time()))

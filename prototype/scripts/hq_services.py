"""HQ services: UDP echo (keepalives, handshake round trips) and a stream receiver log."""
import socket
import threading
import time
from net import ECHO_PORT, VOICE_PORT, VIDEO_PORT


def echo():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", ECHO_PORT))
    while True:
        data, addr = s.recvfrom(65535)
        s.sendto(data[:64], addr)


def receiver(port, name, follow=False):
    """Stream receiver. With follow=True it also routes replies to the vehicle's service
    address through the tunnel its traffic arrives on (IP_PKTINFO arrival interface),
    as a hub gateway would; TCP acknowledgements need that return path."""
    import struct
    import subprocess
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_PKTINFO, 1)
    s.bind(("0.0.0.0", port))
    via = None
    with open(f"/app/logs/recv_{name}.log", "a", buffering=1) as f:
        while True:
            data, anc, _, _ = s.recvmsg(65535, socket.CMSG_SPACE(12))
            now = time.time()
            if follow:
                for lvl, typ, cd in anc:
                    if lvl == socket.IPPROTO_IP and typ == socket.IP_PKTINFO:
                        ifname = socket.if_indextoname(struct.unpack("I", cd[:4])[0])
                        if ifname != via and ifname.startswith("wg"):
                            subprocess.run(f"ip route replace 10.201.0.1/32 dev {ifname}", shell=True)
                            via = ifname
            try:
                run_id, seq, sent = data[:64].split(b"|")[:3]
                f.write(f"{run_id.decode()},{int(seq)},{float(sent):.6f},{now:.6f},{len(data)}\n")
            except ValueError:
                pass


def rx_report():
    """Receiver report: bytes received per tunnel, published every 100 ms (like RTCP/ACK clocking).
    """
    import json
    import os
    while True:
        rep = {"t": time.time()}
        for dev in ("wga", "wgb", "wgm"):
            try:
                rep[dev] = int(open(f"/sys/class/net/{dev}/statistics/rx_bytes").read())
            except OSError:
                pass

        with open("/app/run/hq_rx.tmp", "w") as f:
            json.dump(rep, f)
        os.replace("/app/run/hq_rx.tmp", "/app/run/hq_rx.json")
        time.sleep(0.1)


def bulk_sink():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", 5002))
    s.listen(8)

    def drain(c):
        try:
            while c.recv(65536):
                pass
        except OSError:
            pass
    while True:
        c, _ = s.accept()
        threading.Thread(target=drain, args=(c,), daemon=True).start()


threading.Thread(target=echo, daemon=True).start()
threading.Thread(target=rx_report, daemon=True).start()
threading.Thread(target=bulk_sink, daemon=True).start()
threading.Thread(target=receiver, args=(VOICE_PORT, "voice", True), daemon=True).start()
threading.Thread(target=receiver, args=(VIDEO_PORT, "video"), daemon=True).start()
while True:
    time.sleep(3600)

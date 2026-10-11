#!/usr/bin/env python3
"""
live_server.py — host-side live telemetry feed for the Never Go Dark console's LIVE view.

The prototype directory is bind-mounted into both Docker containers, so every file a run
writes under ./logs and ./run is live on the HOST. This server tails those files, merges
them into one per-tick state object, and serves it to the browser as Server-Sent Events:

    GET /stream   text/event-stream   one `data: {…}` line ~5 Hz (the live feed)
    GET /state    application/json     one-shot snapshot (poll fallback / debugging)
    GET /         text/html            a built-in smoke-test page (verify without the console)

It is READ-ONLY: it writes nothing, opens no sockets to the containers, and never touches a
run. Stdlib only. Run it on the HOST, beside run_demo.sh:

    python3 live_server.py                 # auto-follows the newest run
    PORT=8099 python3 live_server.py       # change the port
    RUN=ngd_corkgeo_r2 python3 live_server.py   # pin to one run id

Contract (the state object, the honesty notes): HANDOVER-CONSOLE/assets/LIVE-TELEMETRY-CONTRACT.md
"""
import glob
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, "logs")
RUN = os.path.join(HERE, "run")
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8099"))
PIN_RUN = os.environ.get("RUN")          # optional: pin to one run id instead of auto-follow
TICK_HZ = 10                             # monitor sampling rate
SSE_HZ = 5                               # browser push rate
NOMINAL_WARM_TH = 0.55                   # DISPLAY reference only; the shipped policy's warm
                                         # threshold is adaptive per link (label it "nominal").
GAP_S = 0.20                             # >=10 consecutive lost 20 ms voice frames = voice-dark
                                         # (matches analyze.py's dark-gap definition)
IDLE_AFTER_S = 3.0                       # ctl file silent this long => run ended
B_PROVENANCE = os.environ.get("B_PROVENANCE", "model")   # bearer-B label the console shows:
                                         # model (GEO, default) | real | real_simultaneous


def _f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _i(x, d=None):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return d


class Monitor:
    """Follows one run and keeps `self.state` (the merged tick object) current."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state = {"status": "idle", "run": None, "mode": None, "t": 0.0}
        self.run = None
        self.mode = None
        self._reset(None)

    def _reset(self, run):
        self.run = run
        self.mode = run.split("_")[0] if run else None
        # ctl tail
        self._ctl_off = 0
        self._ctl_buf = ""
        self._ctl_t = 0.0
        self._events = []
        self._warm = "cold"
        self._active = "a"
        self._p = None
        self._hs_last = None
        # goodput baseline (hq_rx.json is cumulative since container boot)
        self._gp_prev = None
        self._gp = {}
        # voice tail: seek to END so we only track NEW frames for this run
        vp = os.path.join(LOGS, "recv_voice.log")
        self._voice_off = os.path.getsize(vp) if os.path.exists(vp) else 0
        self._voice_buf = ""
        self._last_voice_recv = None
        self._last_voice_owd = None
        self._voice_recv_times = []

    # ---- run discovery -------------------------------------------------
    def _newest_run(self):
        if PIN_RUN:
            p = os.path.join(LOGS, "ctl_%s.csv" % PIN_RUN)
            return (PIN_RUN, os.path.getmtime(p)) if os.path.exists(p) else (PIN_RUN, 0)
        files = glob.glob(os.path.join(LOGS, "ctl_*.csv"))
        if not files:
            return None, 0
        f = max(files, key=os.path.getmtime)
        return os.path.basename(f)[4:-4], os.path.getmtime(f)   # strip "ctl_" and ".csv"

    # ---- ingest --------------------------------------------------------
    def _tail_ctl(self):
        p = os.path.join(LOGS, "ctl_%s.csv" % self.run)
        if not os.path.exists(p):
            return
        if os.path.getsize(p) < self._ctl_off:
            self._ctl_off = 0
        with open(p, "r") as fh:
            fh.seek(self._ctl_off)
            self._ctl_buf += fh.read()
            self._ctl_off = fh.tell()
        *lines, self._ctl_buf = self._ctl_buf.split("\n")
        for ln in lines:
            self._apply_ctl(ln.strip())

    def _apply_ctl(self, ln):
        if not ln or ln.startswith("t,event"):
            return
        parts = ln.split(",", 2)
        if len(parts) < 2:
            return
        t = _f(parts[0], 0.0)
        ev = parts[1]
        detail = parts[2] if len(parts) > 2 else ""
        self._ctl_t = max(self._ctl_t, t)
        if ev == "p":                       # frequent risk sample (ngd only): don't flood ticker
            self._p = _f(detail)
            return
        tgt = detail.replace("wg", "")      # route/roam target: a | b | m
        if ev == "route":
            self._active = "b" if tgt == "b" else "a"
            if tgt == "b":
                self._warm = "carrying"
        elif ev == "roam":                  # mobike: wgm -> bearer_b
            self._active = "b"
            self._warm = "carrying"
        elif ev in ("predict", "hs_start"):
            if self._warm not in ("carrying",):
                self._warm = "warming"
            elif ev == "predict":           # warming the NEXT standby after a cutover
                self._warm = "warming"
        elif ev == "hs_done":
            self._hs_last = _f(detail.rstrip("s"))
            if self._warm == "warming":
                self._warm = "ready"
        elif ev == "cutover":
            self._warm = "carrying"
            self._active = "b"
        elif ev == "teardown":
            self._warm = "cold"
        if ev not in ("ka", "base_rtt", "modem", "summary"):
            self._events.append("%.1fs %s %s" % (t, ev, detail))
            self._events = self._events[-40:]

    def _read_link(self):
        p = os.path.join(LOGS, "link_%s_hq.csv" % self.run)
        if not os.path.exists(p):
            return None, None
        try:
            lines = open(p, "r").read().splitlines()
        except OSError:
            return None, None
        if len(lines) < 2:
            return None, None
        hdr = lines[0].split(",")
        last = lines[-1].split(",")
        if len(last) < len(hdr) and len(lines) >= 3:   # partial trailing line
            last = lines[-2].split(",")
        if len(last) < len(hdr):
            return None, None
        row = dict(zip(hdr, last))
        has_b = "b_usable" in row and row.get("b_usable") != ""
        link = {
            "a_usable": _i(row.get("a_usable")) == 1,
            "a_rate_mbit": _f(row.get("a_rate_mbit")),
            "a_owd_ms": _f(row.get("a_owd_ms")),
            "a_loss_pct": _f(row.get("a_loss_pct")),
            "net_mode": row.get("net_mode"),
            "rsrp_dbm": _i(row.get("rsrp_dbm")),
            "b_usable": (_i(row.get("b_usable")) == 1) if has_b else None,
            "b_rate_mbit": _f(row.get("b_rate_mbit")) if has_b else None,
            "b_owd_ms": _f(row.get("b_owd_ms")) if has_b else None,
        }
        return _i(row.get("k")), link

    def _read_goodput(self):
        """Returns the container clock (hq_rx 't'); updates per-tunnel Mbps from byte deltas."""
        try:
            d = json.load(open(os.path.join(RUN, "hq_rx.json")))
        except (OSError, ValueError):
            return None
        tnow = _f(d.get("t"))
        now_bytes = {k: d[k] for k in d if k.startswith("wg")}
        if self._gp_prev and tnow is not None:
            t0, b0 = self._gp_prev
            dt = tnow - t0
            if dt > 0.05:
                self._gp = {k: max(0.0, (now_bytes[k] - b0.get(k, now_bytes[k])) * 8.0 / dt / 1e6)
                            for k in now_bytes}
                self._gp_prev = (tnow, now_bytes)
        else:
            self._gp_prev = (tnow, now_bytes)
        return tnow

    def _tail_voice(self):
        p = os.path.join(LOGS, "recv_voice.log")
        if not os.path.exists(p):
            return
        if os.path.getsize(p) < self._voice_off:
            self._voice_off = 0
        with open(p, "r") as fh:
            fh.seek(self._voice_off)
            self._voice_buf += fh.read()
            self._voice_off = fh.tell()
        *lines, self._voice_buf = self._voice_buf.split("\n")
        for ln in lines:
            parts = ln.split(",")               # run,seq,sent,recv,size
            if len(parts) < 5 or parts[0] != self.run:
                continue
            sent, recv = _f(parts[2]), _f(parts[3])
            if recv is None:
                continue
            self._last_voice_recv = recv
            self._last_voice_owd = (recv - sent) * 1000.0 if sent is not None else None
            self._voice_recv_times.append(recv)
        if self._voice_recv_times:               # keep ~1 s for the pps count
            cut = self._voice_recv_times[-1] - 1.0
            self._voice_recv_times = [x for x in self._voice_recv_times if x >= cut]

    # ---- one tick ------------------------------------------------------
    def tick(self):
        with self.lock:
            run, mtime = self._newest_run()
            if run != self.run:
                if not run:
                    self.state = {"status": "idle", "run": None, "mode": None, "t": 0.0,
                                  "note": "no run yet — start one with ./run_demo.sh <mode>"}
                    return
                self._reset(run)

            self._tail_ctl()
            k, link = self._read_link()
            cnow = self._read_goodput()          # container clock
            self._tail_voice()

            if cnow is None:
                cnow = self._last_voice_recv
            dark, gap_ms = False, 0.0
            if self._last_voice_recv is not None and cnow is not None:
                since = cnow - self._last_voice_recv
                dark = since > GAP_S
                gap_ms = max(0.0, since * 1000.0) if dark else 0.0

            t = float(k) if k is not None else round(self._ctl_t, 1)
            live = bool(mtime) and (time.time() - mtime) < IDLE_AFTER_S
            self.state = {
                "status": "live" if live else "ended",
                "run": run,
                "mode": self.mode,
                "t": round(t, 1),
                "bearer": self._active,
                "p": round(self._p, 3) if (self.mode == "ngd" and self._p is not None) else None,
                "p_warm_th": NOMINAL_WARM_TH if self.mode == "ngd" else None,
                "warm_state": self._warm,
                "hs_last_s": self._hs_last,
                "link": link or {},
                "b_provenance": B_PROVENANCE,
                "goodput_mbps": {k2: round(v, 2) for k2, v in self._gp.items()},
                "voice": {
                    "recv_pps": len(self._voice_recv_times),
                    "owd_ms": round(self._last_voice_owd, 1) if self._last_voice_owd is not None else None,
                    "ok": not dark,
                    "dark_gap": bool(dark),
                    "gap_ms": round(gap_ms),
                },
                "recent_events": list(reversed(self._events[-8:])),
            }


MON = Monitor()


def _monitor_loop():
    period = 1.0 / TICK_HZ
    while True:
        try:
            MON.tick()
        except Exception:                        # never let the monitor die mid-demo
            pass
        time.sleep(period)


SMOKE_HTML = """<!doctype html><meta charset=utf-8><title>NGD live feed</title>
<style>body{background:#0A0D12;color:#E8ECF2;font:14px/1.5 ui-monospace,Menlo,monospace;padding:20px}
b{color:#FF9D3C}pre{white-space:pre-wrap;color:#8C98A8}</style>
<h2>Never Go Dark &mdash; <b>live feed</b> smoke test</h2>
<div>status: <b id=s>connecting…</b> &nbsp; t: <b id=t>-</b>s &nbsp; bearer: <b id=b>-</b>
 &nbsp; warm: <b id=w>-</b> &nbsp; p: <b id=p>-</b> &nbsp; voice: <b id=v>-</b></div>
<pre id=j></pre>
<script>
var es=new EventSource('/stream');
es.onmessage=function(e){var d=JSON.parse(e.data);
 document.getElementById('s').textContent=d.status;
 document.getElementById('t').textContent=d.t;
 document.getElementById('b').textContent=d.bearer||'-';
 document.getElementById('w').textContent=d.warm_state||'-';
 document.getElementById('p').textContent=d.p==null?'n/a (ngd only)':d.p;
 document.getElementById('v').textContent=d.voice?(d.voice.dark_gap?('DARK '+d.voice.gap_ms+'ms'):('ok '+d.voice.recv_pps+'pps')):'-';
 document.getElementById('j').textContent=JSON.stringify(d,null,2);};
es.onerror=function(){document.getElementById('s').textContent='no connection to the feed server';};
</script>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass                                     # quiet

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self._cors()
            self.end_headers()
            try:
                while True:
                    with MON.lock:
                        payload = json.dumps(MON.state, separators=(",", ":"))
                    self.wfile.write(b"data: " + payload.encode("utf-8") + b"\n\n")
                    self.wfile.flush()
                    time.sleep(1.0 / SSE_HZ)
            except (BrokenPipeError, ConnectionResetError):
                return
        elif path == "/state":
            with MON.lock:
                payload = json.dumps(MON.state).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._cors()
            self.end_headers()
            self.wfile.write(payload)
        elif path == "/":
            body = SMOKE_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


def main():
    threading.Thread(target=_monitor_loop, daemon=True).start()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print("NGD live feed  →  http://%s:%d   (/stream · /state · /)" % (HOST, PORT))
    print("  logs: %s" % LOGS)
    print("  following: %s" % (PIN_RUN or "newest run (auto)"))
    print("  read-only; Ctrl-C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()

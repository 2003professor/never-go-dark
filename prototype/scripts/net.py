"""Shared addressing plan and small helpers for the KATIM continuity prototype."""
import subprocess

UNDERLAY = {"hq": {"a": "10.10.1.20", "b": "10.10.2.20"},
            "vehicle": {"a": "10.10.1.10", "b": "10.10.2.10"}}
PORT = {"a": 51821, "b": 51822, "m": 51823}
INNER = {"a": "10.99.1", "b": "10.99.2", "m": "10.99.3"}
HOST = {"hq": "20", "vehicle": "10"}
SERVICE = {"hq": "10.200.0.1", "vehicle": "10.201.0.1"}
ECHO_PORT = 9000
VOICE_PORT = 5000
VIDEO_PORT = 5001
KEYS = "/app/run/keys"


def sh(cmd, check=True):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{cmd}\n{r.stderr}")
    return r.stdout.strip()


def pub(role, n):
    return open(f"{KEYS}/{role}.{n}.pub").read().strip()


def underlay_dev(prefix):
    for line in sh("ip -o -4 addr show").splitlines():
        parts = line.split()
        if parts[3].startswith(prefix):
            return parts[1]
    raise RuntimeError(f"no device for {prefix}")


def inner_ip(n, role):
    return f"{INNER[n]}.{HOST[role]}"

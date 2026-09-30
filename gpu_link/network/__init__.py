"""Network interfaces and optional bounded UDP discovery."""

import json
import secrets
import socket
import threading
import time

import psutil

from gpu_link.protocol import VERSION
from gpu_link.security import lan_address

DISCOVERY_PORT = 8766


def interfaces():
    result = []
    for name, entries in psutil.net_if_addrs().items():
        for entry in entries:
            if entry.family == socket.AF_INET and lan_address(entry.address):
                result.append({"name": name, "ip": entry.address, "broadcast": entry.broadcast})
    return result


class DiscoveryResponder:
    def __init__(self, host, port, gpu_name, vram):
        self.stop_event = threading.Event()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", DISCOVERY_PORT))
        self.sock.settimeout(0.5)
        self.info = {"protocol": VERSION, "hostname": socket.gethostname(), "host": host,
                     "port": port, "gpu": gpu_name, "vram": vram}
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        last = {}
        while not self.stop_event.is_set():
            try:
                raw, addr = self.sock.recvfrom(2048)
                now = time.monotonic()
                if not lan_address(addr[0]) or now - last.get(addr[0], 0) < 0.5:
                    continue
                if len(last) >= 256:
                    last.clear()
                last[addr[0]] = now
                request = json.loads(raw)
                nonce = request.get("nonce")
                if request.get("discover") == VERSION and isinstance(nonce, str) and len(nonce) == 32:
                    self.sock.sendto(json.dumps({**self.info, "nonce": nonce}).encode(), addr)
            except (OSError, ValueError, AttributeError):
                continue

    def stop(self):
        self.stop_event.set()
        self.sock.close()


def discover(seconds=2):
    nonce = secrets.token_hex(16)
    request = json.dumps({"discover": VERSION, "nonce": nonce}).encode()
    found = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.2)
        start = time.monotonic()
        targets = {"255.255.255.255"} | {x["broadcast"] for x in interfaces() if x["broadcast"]}
        for target in targets:
            try:
                sock.sendto(request, (target, DISCOVERY_PORT))
            except OSError:
                pass
        while time.monotonic() - start < seconds:
            try:
                raw, addr = sock.recvfrom(2048)
                info = json.loads(raw)
                if (info.get("protocol") == VERSION and info.get("nonce") == nonce
                        and lan_address(addr[0]) and lan_address(info.get("host", ""))):
                    info["discovery_ms"] = (time.monotonic() - start) * 1000
                    found[(info["host"], info["port"])] = info
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                continue
    return list(found.values())

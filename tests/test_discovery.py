import json
import socket

from gpu_link.network import DISCOVERY_PORT, discover


def test_discovery_validation(monkeypatch):
    class FakeSocket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def setsockopt(self, *args): pass
        def settimeout(self, *args): pass
        def sendto(self, raw, addr):
            self.nonce = json.loads(raw)["nonce"]
            assert addr[1] == DISCOVERY_PORT
        def recvfrom(self, limit):
            return json.dumps({"protocol": "GPU-LINK/1", "nonce": self.nonce,
                "host": "192.168.1.25", "port": 8765, "hostname": "TEST-WORKER"}).encode(), ("192.168.1.25", 8766)
    monkeypatch.setattr(socket, "socket", lambda *args: FakeSocket())
    monkeypatch.setattr("gpu_link.network.interfaces", lambda: [])
    found = discover(0.01)
    assert len(found) == 1 and found[0]["hostname"] == "TEST-WORKER"


def test_discovery_timeout(monkeypatch):
    class FakeSocket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def setsockopt(self, *args): pass
        def settimeout(self, *args): pass
        def sendto(self, *args): pass
        def recvfrom(self, *args): raise TimeoutError()
    monkeypatch.setattr(socket, "socket", lambda *args: FakeSocket())
    monkeypatch.setattr("gpu_link.network.interfaces", lambda: [])
    assert discover(0.01) == []

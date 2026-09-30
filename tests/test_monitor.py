import time

from PySide6.QtCore import QCoreApplication, Qt

from gpu_link.security import Identity
from gpu_link.ui.tasks import Engine
from gpu_link.worker import Worker


class NoGPU:
    def info(self): return self.telemetry()
    def telemetry(self): return {"gpus": [], "driver_available": False}


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("Expected monitor transition did not occur")


def test_automatic_reconnect(monkeypatch, tmp_path):
    app = QCoreApplication.instance() or QCoreApplication([])
    assert app is not None
    monkeypatch.setattr("gpu_link.ui.tasks.GPU", NoGPU)
    identity = Identity(tmp_path)
    service = Worker("127.0.0.1", 0, identity, NoGPU(), False, True)
    service.start()
    engine = Engine()
    events = []
    engine.event.connect(lambda kind, value: events.append((kind, value)), Qt.ConnectionType.DirectConnection)
    engine.start()
    replacement = None
    try:
        engine.submit("connect", host="127.0.0.1", port=service.port, token=identity.token,
                      fingerprint=identity.fingerprint, allow_loopback=True)
        wait_for(lambda: ("status", "CONNECTED") in events)
        service.stop()
        wait_for(lambda: any(k == "status" and "CONNECTION LOST" in v for k, v in events))
        replacement = Worker("127.0.0.1", service.port, identity, NoGPU(), False, True)
        replacement.start()
        wait_for(lambda: ("status", "RECONNECTED") in events)
    finally:
        engine.stop_event.set()
        assert engine.wait(6000)
        service.stop()
        if replacement:
            replacement.stop()

import ssl
import threading
import time

import pytest

from gpu_link.controller import Client, backoff
from gpu_link.security import Identity, parse_invitation
from gpu_link.worker import Worker
from gpu_link.worker.jobs import Jobs


class NoGPU:
    def info(self):
        return {**self.telemetry(), "torch_cuda": False, "runtime_available": False}

    def telemetry(self):
        return {"gpus": [], "driver_available": False}


@pytest.fixture
def worker(tmp_path):
    service = Worker("127.0.0.1", 0, Identity(tmp_path), NoGPU(), False, True)
    service.start()
    yield service
    service.stop()


def client_for(worker, token=None, fingerprint=None):
    return Client("127.0.0.1", worker.port, token or worker.identity.token,
                  fingerprint or worker.identity.fingerprint, True)


def test_authentication_and_telemetry(worker):
    client = client_for(worker)
    client.connect()
    assert client.request("heartbeat")["uptime"] >= 0
    assert client.request("get_gpu_info")["gpus"] == []
    client.close()


def test_invalid_token(worker):
    client = client_for(worker, token="invalid")
    with pytest.raises(RuntimeError, match="Authentication"):
        client.connect()


def test_certificate_pin_rejected(worker):
    client = client_for(worker, fingerprint="0" * 64)
    with pytest.raises(ssl.SSLError):
        client.connect()


def test_command_allowlist(worker):
    client = client_for(worker)
    client.connect()
    with pytest.raises((ConnectionError, OSError)):
        client.request("run_shell", command="anything")
    client.close()


def test_network_transfers(worker):
    client = client_for(worker)
    client.connect()
    for direction in ("upload", "download"):
        result = client.bandwidth(direction, 1024**2)
        assert result["bytes"] == 1024**2
        assert result["mbps"] > 0
    client.close()


def test_connection_loss_and_reconnection(worker):
    client = client_for(worker)
    client.connect()
    with worker.lock:
        for peer in worker.peers:
            peer.shutdown(2)
            peer.close()
    with pytest.raises((ConnectionError, OSError)):
        client.request("heartbeat")
    client.connect()
    assert client.request("heartbeat")["uptime"] >= 0
    client.close()
    assert [backoff(i) for i in range(8)] == [1, 2, 4, 8, 16, 30, 30, 30]


def test_identity_persists(tmp_path):
    a = Identity(tmp_path)
    b = Identity(tmp_path)
    assert a.token == b.token and a.fingerprint == b.fingerprint
    assert a.token.encode() not in (tmp_path / "worker.secret").read_bytes()
    info = parse_invitation(a.invitation("192.168.1.2", 8765))
    assert info["token"] == a.token


def test_cancelled_job_and_busy(monkeypatch):
    started = threading.Event()
    def fake_stress(seconds, cancel, gpu):
        started.set()
        assert cancel.wait(3)
        return {"cancelled": True}
    monkeypatch.setattr("gpu_link.worker.jobs.stress", fake_stress)
    jobs = Jobs(NoGPU())
    job = jobs.submit("run_stress_test", {"seconds": 30})
    assert started.wait(1)
    with pytest.raises(ValueError, match="busy"):
        jobs.submit("run_memory_test", {})
    jobs.cancel.set()
    for _ in range(100):
        if jobs.get(job["job_id"])["status"] != "running":
            break
        time.sleep(0.01)
    assert jobs.get(job["job_id"])["status"] == "cancelled"
    assert jobs.completed == 0


def test_lost_controller_lease(monkeypatch):
    monkeypatch.setattr("gpu_link.worker.jobs.stress", lambda seconds, cancel, gpu:
                        {"cancelled": cancel.wait(3)})
    jobs = Jobs(NoGPU())
    jobs.submit("run_stress_test", {"seconds": 30})
    jobs.lease = time.monotonic() - 20
    assert jobs.cancel.wait(2)


@pytest.mark.parametrize("op,args", [("run_compute_test", {"seed": -1}),
    ("run_compute_test", {"seed": True}), ("run_stress_test", {"seconds": 9999})])
def test_invalid_job(op, args):
    with pytest.raises(ValueError):
        Jobs(NoGPU()).submit(op, args)


def test_protocol_version_rejected(worker, monkeypatch):
    monkeypatch.setattr("gpu_link.controller.VERSION", "GPU-LINK/999")
    client = client_for(worker)
    with pytest.raises((ConnectionError, OSError)):
        client.connect()


def test_gpu_job_failure_reaches_controller(worker, monkeypatch):
    def unavailable():
        raise RuntimeError("CUDA unavailable: no CPU fallback")
    monkeypatch.setattr("gpu_link.worker.jobs.memory_test", unavailable)
    client = client_for(worker)
    client.connect()
    try:
        with pytest.raises(RuntimeError, match="no CPU fallback"):
            client.job("run_memory_test")
    finally:
        client.close()

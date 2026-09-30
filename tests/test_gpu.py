import base64
import hashlib

import numpy as np
import pytest

from gpu_link.benchmarks import compute, matrices, memory_test, verify
from gpu_link.gpu import GPU, cuda_status, require_cuda


def reference(seed):
    a, b = matrices(seed)
    raw = (a @ b).astype("<f4").tobytes()
    return {"seed": seed, "dimensions": [256] * 3, "backend": "cuda:0", "device": 0,
            "result": base64.b64encode(raw).decode(), "sha256": hashlib.sha256(raw).hexdigest()}


def test_independent_verification():
    assert verify(reference(42), 42)
    assert not verify(reference(42), 43)


def test_corrupted_result_even_with_valid_hash():
    result = reference(5)
    raw = np.zeros((256, 256), dtype="<f4").tobytes()
    result.update(result=base64.b64encode(raw).decode(), sha256=hashlib.sha256(raw).hexdigest())
    assert not verify(result, 5)


def test_cpu_result_rejected():
    result = reference(5)
    result["backend"] = "cpu"
    assert not verify(result, 5)


def test_missing_gpu_telemetry():
    gpu = GPU.__new__(GPU)
    gpu.nvml = None
    gpu.error = "NVML absent"
    assert gpu.telemetry()["gpus"] == []
    assert not gpu.telemetry()["driver_available"]


def test_cuda_unavailable(monkeypatch):
    class FakeTorch:
        class version:
            cuda = None
    monkeypatch.setattr("gpu_link.gpu.torch_module", lambda: FakeTorch)
    with pytest.raises(RuntimeError, match="never fall back"):
        require_cuda()


def test_telemetry_units():
    import threading
    from types import SimpleNamespace
    class NVML:
        NVMLError = RuntimeError
        NVML_TEMPERATURE_GPU = 0
        def nvmlSystemGetDriverVersion(self): return "test-driver"
        def nvmlDeviceGetCount(self): return 1
        def nvmlDeviceGetHandleByIndex(self, index): return 0
        def nvmlDeviceGetName(self, handle): return "TEST FIXTURE"
        def nvmlDeviceGetUUID(self, handle): return "fixture-uuid"
        def nvmlDeviceGetMemoryInfo(self, handle): return SimpleNamespace(total=1024, used=256, free=768)
        def nvmlDeviceGetUtilizationRates(self, handle): return SimpleNamespace(gpu=37)
        def nvmlDeviceGetPowerUsage(self, handle): return 125000
        def nvmlDeviceGetTemperature(self, handle, sensor): return 55
    gpu = GPU.__new__(GPU)
    gpu.nvml, gpu.error, gpu.lock = NVML(), "", threading.Lock()
    value = gpu.telemetry()["gpus"][0]
    assert value["power"] == 125 and value["used"] == 256 and value["temperature"] == 55


@pytest.mark.gpu
def test_real_cuda_compute_and_memory():
    if not cuda_status()["torch_cuda"]:
        pytest.skip("Actual CUDA hardware unavailable")
    assert memory_test()["passed"]
    result = compute(1977)
    assert result["backend"] == "cuda:0"
    assert result["peak_allocated"] > 0
    assert verify(result, 1977)


@pytest.mark.parametrize("temperature", [85, 95, None])
def test_stress_thermal_safety(monkeypatch, temperature):
    import threading
    from types import SimpleNamespace
    from gpu_link.benchmarks import stress
    fake_torch = SimpleNamespace(randn=lambda *a, **kw: object(), randn_like=lambda x: object())
    monkeypatch.setattr("gpu_link.benchmarks.require_cuda", lambda: fake_torch)
    gpu = SimpleNamespace(telemetry=lambda: {"gpus": [{"temperature": temperature}]})
    with pytest.raises(RuntimeError, match="[Tt]emperature"):
        stress(30, threading.Event(), gpu)

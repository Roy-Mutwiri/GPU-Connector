"""Deterministic matrix challenges verified fully on the controller CPU."""

import base64
import hashlib
import time

import numpy as np

from gpu_link.gpu import require_cuda

MATRIX_SIZE = 256


def matrices(seed, size=MATRIX_SIZE):
    rng = np.random.default_rng(seed)
    return tuple(rng.integers(-3, 4, (size, size), dtype=np.int32).astype(np.float32) for _ in range(2))


def compute(seed):
    torch = require_cuda()
    a, b = matrices(seed)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.cuda.set_device(0)
    torch.cuda.reset_peak_memory_stats(0)
    x, y = torch.from_numpy(a).to("cuda:0"), torch.from_numpy(b).to("cuda:0")
    torch.cuda.synchronize(0)
    start = time.perf_counter()
    z = x @ y
    torch.cuda.synchronize(0)
    elapsed = time.perf_counter() - start
    raw = z.cpu().numpy().astype("<f4").tobytes()
    return {"gpu": torch.cuda.get_device_name(0), "device": 0, "backend": str(z.device),
            "dimensions": [MATRIX_SIZE] * 3, "seed": seed, "seconds": elapsed,
            "sha256": hashlib.sha256(raw).hexdigest(), "result": base64.b64encode(raw).decode(),
            "allocated": torch.cuda.memory_allocated(0), "peak_allocated": torch.cuda.max_memory_allocated(0)}


def verify(result, seed):
    try:
        if result["seed"] != seed or result["dimensions"] != [MATRIX_SIZE] * 3:
            return False
        if result["backend"] != "cuda:0" or result["device"] != 0:
            return False
        raw = base64.b64decode(result["result"], validate=True)
        if len(raw) != MATRIX_SIZE * MATRIX_SIZE * 4 or hashlib.sha256(raw).hexdigest() != result["sha256"]:
            return False
        a, b = matrices(seed)
        expected = a.astype(np.int64) @ b.astype(np.int64)
        actual = np.frombuffer(raw, dtype="<f4").reshape(a.shape)
        return bool(np.array_equal(actual, expected))
    except (KeyError, ValueError, TypeError):
        return False


def memory_test():
    torch = require_cuda()
    block = torch.full((16 * 1024 * 1024,), 0.125, dtype=torch.float32, device="cuda:0")
    torch.cuda.synchronize(0)
    passed = bool(torch.all(block == 0.125).item())
    return {"passed": passed, "bytes": block.numel() * block.element_size(), "device": str(block.device)}


def stress(seconds, cancel, gpu):
    torch = require_cuda()
    x = torch.randn((2048, 2048), device="cuda:0")
    y = torch.randn_like(x)
    started, iterations = time.monotonic(), 0
    while time.monotonic() - started < seconds:
        if cancel.is_set():
            return {"cancelled": True, "iterations": iterations}
        temperatures = [g["temperature"] for g in gpu.telemetry()["gpus"] if g["temperature"] is not None]
        if not temperatures:
            raise RuntimeError("Temperature telemetry unavailable; stress test stopped for safety")
        if max(temperatures) >= 85:
            raise RuntimeError("Temperature reached 85 C safety threshold; stress test stopped")
        for _ in range(8):
            z = x @ y
        torch.cuda.synchronize(0)
        if not bool(torch.isfinite(z).all().item()):
            raise RuntimeError("CUDA produced non-finite output")
        iterations += 8
        cancel.wait(0.01)
    return {"cancelled": False, "iterations": iterations, "seconds": time.monotonic() - started,
            "gpu": torch.cuda.get_device_name(0), "device": 0}

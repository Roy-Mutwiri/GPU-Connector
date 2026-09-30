"""Individually reported full health and in-memory network benchmarks."""

import secrets
import statistics
import time

from gpu_link.benchmarks import verify

STAGES = ["TCP connection", "Authentication", "API/protocol handshake", "Worker heartbeat",
          "NVIDIA driver", "CUDA runtime", "PyTorch CUDA", "Remote GPU allocation",
          "Remote GPU compute", "Result verification", "Network latency", "Upload bandwidth",
          "Download bandwidth", "Sustained connection", "Worker telemetry"]


def network_test(client, emit, cancel):
    samples, failures = [], 0
    for _ in range(20):
        if cancel.is_set():
            raise InterruptedError("Test stopped")
        start = time.perf_counter()
        try:
            client.request("heartbeat")
            samples.append((time.perf_counter() - start) * 1000)
        except (OSError, RuntimeError, ValueError):
            failures += 1
        time.sleep(0.05)
    if not samples:
        raise ConnectionError("All latency requests failed")
    latency = statistics.mean(samples)
    jitter = statistics.pstdev(samples)
    emit("Network latency", f"{latency:.2f} ms | jitter σ {jitter:.2f} ms | failures {failures}/20")
    result = {"latency_ms": latency, "jitter_ms": jitter, "failures": failures, "transfers": {}}
    for direction in ("upload", "download"):
        rows = []
        for mib in (1, 16, 64, 256):
            if cancel.is_set():
                raise InterruptedError("Test stopped")
            row = {"MiB": mib, **client.bandwidth(direction, mib * 1024 * 1024)}
            rows.append(row)
            emit(direction.title() + " bandwidth", f"{mib} MiB: {row['mbps']:.1f} Mbps / {row['MB_s']:.1f} MB/s")
        result["transfers"][direction] = rows
    rate = min(result["transfers"][d][-1]["mbps"] for d in ("upload", "download"))
    result["quality"] = "EXCELLENT" if not failures and latency < 5 and rate >= 800 else (
        "GOOD" if not failures and latency < 20 and rate >= 100 else "LIMITED")
    return result


def full_test(client, emit, cancel, progress=None):
    result = {"compute_verified": False, "memory_verified": False, "network": None}
    for stage in STAGES:
        emit(stage, "WAITING")
    client.connect(emit)
    client.request("heartbeat")
    emit("Worker heartbeat", "PASS")
    info = client.request("get_gpu_info")
    for label, key in (("NVIDIA driver", "driver_available"), ("CUDA runtime", "runtime_available"),
                       ("PyTorch CUDA", "torch_cuda")):
        emit(label, "PASS" if info.get(key) else "FAIL — " + (info.get("error") or "Unavailable"))
    if info.get("torch_cuda"):
        try:
            memory = client.job("run_memory_test", cancel, progress)
            result["memory_verified"] = memory.get("passed") is True and memory.get("device") == "cuda:0"
            emit("Remote GPU allocation", "PASS — 64 MiB written/read" if result["memory_verified"] else "FAIL")
            seed = secrets.randbits(32)
            computed = client.job("run_compute_test", cancel, progress, seed=seed)
            emit("Remote GPU compute", f"PASS — {computed['gpu']} | CUDA {computed['device']} | {computed['seconds']*1000:.3f} ms")
            result["compute_verified"] = verify(computed, seed)
            result["compute"] = {k: v for k, v in computed.items() if k != "result"}
            emit("Result verification", "PASS — all 65,536 values match" if result["compute_verified"] else "FAIL")
        except InterruptedError:
            raise
        except Exception as exc:
            if not result["memory_verified"]:
                emit("Remote GPU allocation", f"FAIL — {exc}")
            emit("Remote GPU compute", f"FAIL — {exc}")
            emit("Result verification", "FAIL — no verified result")
    else:
        for stage in ("Remote GPU allocation", "Remote GPU compute", "Result verification"):
            emit(stage, "SKIPPED — CUDA unavailable (not a pass)")
    result["network"] = network_test(client, emit, cancel)
    start, count = time.monotonic(), 0
    while time.monotonic() - start < 10:
        if cancel.wait(0.25):
            raise InterruptedError("Test stopped")
        client.request("heartbeat")
        count += 1
    emit("Sustained connection", f"PASS — 10 seconds, {count} heartbeats")
    telemetry = client.request("get_telemetry")
    emit("Worker telemetry", "PASS" if telemetry.get("gpus") else "FAIL — NVML GPU telemetry unavailable")
    result["ready"] = bool(result["compute_verified"] and result["memory_verified"] and
                           info.get("runtime_available") and info.get("driver_available") and
                           telemetry.get("gpus") and result["network"]["failures"] == 0)
    return result

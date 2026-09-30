"""Actionable runtime diagnostics; firewall reachability is measured remotely."""

import platform
import socket

from gpu_link.network import interfaces


def diagnose(gpu, worker=None, connected=False):
    info = gpu.info()
    return {"Windows / OS": platform.platform(), "Python/runtime": platform.python_version(),
        "Hostname": socket.gethostname(), "NVIDIA driver": info.get("driver") or "Unavailable — install NVIDIA driver",
        "nvidia-smi": "Available" if info["nvidia_smi"] else "Not on PATH (not a CUDA test)",
        "NVML": "Available" if info["nvml_available"] else "Unavailable — check driver and nvidia-ml-py",
        "PyTorch": info.get("torch_version", "Not installed — install official CUDA build"),
        "PyTorch CUDA": "Available" if info["torch_cuda"] else info.get("error", "Unavailable"),
        "CUDA runtime": str(info.get("runtime_version")) if info["runtime_available"] else "Unavailable",
        "CUDA devices": info["device_count"], "GPUs / VRAM": info["gpus"],
        "Interfaces": interfaces(), "Worker port": worker.port if worker else "Not listening in controller mode",
        "Worker connectivity": "Authenticated connection" if connected else "Not connected",
        "Windows Firewall accessibility": "Confirmed for current connection" if connected else
            "Unverified from another PC. Connect from controller; allow TCP worker port on Private/LocalSubnet only.",
        "Discovery": "UDP 8766 optional; manual connection works without discovery",
        "Compute verification": "Only RUN FULL TEST proves a completed CUDA calculation; driver presence is insufficient."}

"""Real NVIDIA telemetry and independently probed CUDA runtime."""

import ctypes
import functools
import platform
import shutil
import threading
from pathlib import Path


@functools.lru_cache(maxsize=1)
def torch_module():
    import torch
    return torch


@functools.lru_cache(maxsize=1)
def cuda_status():
    result = {"torch_installed": False, "torch_cuda": False, "runtime_available": False,
              "runtime_version": None, "cuda_build": None, "device_count": 0, "error": ""}
    try:
        torch = torch_module()
        result.update(torch_installed=True, torch_version=torch.__version__, cuda_build=torch.version.cuda)
        result["torch_cuda"] = torch.cuda.is_available()
        result["device_count"] = torch.cuda.device_count()
        for path in (Path(torch.__file__).parent / "lib").glob("cudart64*.dll"):
            runtime = ctypes.CDLL(str(path))
            version, count = ctypes.c_int(), ctypes.c_int()
            if runtime.cudaRuntimeGetVersion(ctypes.byref(version)) == 0:
                result["runtime_version"] = version.value
            result["runtime_available"] = runtime.cudaGetDeviceCount(ctypes.byref(count)) == 0 and count.value > 0
            break
        if platform.system() != "Windows":
            result["runtime_available"] = result["torch_cuda"]
            result["runtime_version"] = torch.version.cuda
        if not torch.version.cuda:
            result["error"] = "PyTorch is a CPU build. Install the official CUDA-enabled PyTorch wheel."
        elif not result["torch_cuda"]:
            result["error"] = "CUDA initialization failed. Check NVIDIA driver and restart the worker."
    except Exception as exc:
        result["error"] = f"PyTorch unavailable: {type(exc).__name__}. Install CUDA-enabled PyTorch."
    return result


class GPU:
    def __init__(self):
        self.lock = threading.Lock()
        self.nvml = None
        self.error = ""
        try:
            import pynvml
            pynvml.nvmlInit()
            self.nvml = pynvml
        except Exception as exc:
            self.error = str(exc)

    def telemetry(self):
        result = {"driver_available": False, "nvml_available": self.nvml is not None,
                  "driver": None, "gpus": [], "nvml_error": self.error}
        if self.nvml is None:
            return result
        n = self.nvml
        with self.lock:
            try:
                result["driver"] = str(n.nvmlSystemGetDriverVersion())
                result["driver_available"] = True
                for index in range(n.nvmlDeviceGetCount()):
                    handle = n.nvmlDeviceGetHandleByIndex(index)
                    def read(fn, *args):
                        try:
                            return fn(handle, *args)
                        except n.NVMLError:
                            return None
                    memory = read(n.nvmlDeviceGetMemoryInfo)
                    utilization = read(n.nvmlDeviceGetUtilizationRates)
                    power = read(n.nvmlDeviceGetPowerUsage)
                    result["gpus"].append({"index": index, "name": str(read(n.nvmlDeviceGetName)),
                        "uuid": str(read(n.nvmlDeviceGetUUID)),
                        "total": memory.total if memory else None,
                        "used": memory.used if memory else None, "free": memory.free if memory else None,
                        "utilization": utilization.gpu if utilization else None,
                        "temperature": read(n.nvmlDeviceGetTemperature, n.NVML_TEMPERATURE_GPU),
                        "power": power / 1000 if power is not None else None})
            except n.NVMLError as exc:
                result["nvml_error"] = str(exc)
        return result

    def info(self):
        return {**self.telemetry(), **cuda_status(), "nvidia_smi": shutil.which("nvidia-smi") is not None}


def require_cuda():
    torch = torch_module()
    if not torch.version.cuda or not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable: GPU tests never fall back to CPU")
    return torch

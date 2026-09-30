"""Read-only local runtime discovery and bounded HTTP ZIP range access."""
import ctypes
import hashlib
import io
import os
import shutil
import sys
import urllib.request
from pathlib import Path


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def candidate_roots(destination, extra=None):
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    roots = [Path(destination), local / "Programs" / "GPU Link", Path.cwd() / ".venv",
             Path(sys.executable).parent, Path(sys.prefix)]
    if extra:
        roots.insert(1, Path(extra))
    for name in ("CUDA_PATH", "CONDA_PREFIX", "VIRTUAL_ENV"):
        if os.environ.get(name):
            roots.append(Path(os.environ[name]))
    for exe in ("python", "python3"):
        found = shutil.which(exe)
        if found:
            roots.extend([Path(found).parent, Path(found).parent.parent])
    for parent, pattern in [(local / "Programs" / "Python", "Python*"),
                            (Path.home() / ".conda" / "envs", "*"),
                            (Path.home() / "miniconda3" / "envs", "*"),
                            (Path.home() / "anaconda3" / "envs", "*"),
                            (Path(os.environ.get("ProgramFiles", "C:/Program Files")) /
                             "NVIDIA GPU Computing Toolkit" / "CUDA", "v*")]:
        if parent.is_dir():
            roots.extend(list(parent.glob(pattern))[:64])
    roots.extend([Path.home() / "miniconda3", Path.home() / "anaconda3"])
    if os.name == "nt":
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Python\PythonCore") as key:
                    for index in range(min(winreg.QueryInfoKey(key)[0], 64)):
                        version = winreg.EnumKey(key, index)
                        try:
                            with winreg.OpenKey(key, version + r"\InstallPath") as location:
                                roots.append(Path(winreg.QueryValueEx(location, "")[0]))
                        except OSError:
                            continue
            except OSError:
                pass
    candidates, seen = [], set()
    for root in roots:
        for suffix in ("", "_internal/torch/lib", "Lib/site-packages/torch/lib", "torch/lib", "lib", "bin"):
            path = (root / suffix).resolve()
            if path not in seen and path.is_dir():
                seen.add(path)
                candidates.append(path)
    return candidates


def driver_status():
    if os.name != "nt":
        return "Windows NVIDIA driver check unavailable on this OS"
    path = Path(os.environ.get("SYSTEMROOT", "C:/Windows")) / "System32" / "nvcuda.dll"
    if not path.is_file():
        return "NVIDIA driver missing — install a compatible NVIDIA driver before GPU tests"
    try:
        driver = ctypes.WinDLL(str(path))
        version = ctypes.c_int()
        if driver.cuDriverGetVersion(ctypes.byref(version)) != 0:
            return "NVIDIA driver found; CUDA driver API query failed"
        major, minor = version.value // 1000, version.value % 1000 // 10
        if version.value < 12000:
            return f"NVIDIA driver CUDA API {major}.{minor} — driver update required for CUDA 12.x"
        return f"NVIDIA driver found (CUDA API {major}.{minor}); RUN FULL TEST verifies GPU operation"
    except OSError:
        return "NVIDIA driver DLL could not load — repair/update the NVIDIA driver"


def scan_runtime(manifest, destination, report, cancelled, extra=None, candidates=None):
    paths = candidate_roots(destination, extra) if candidates is None else candidates
    found, checked = {}, set()
    for directory in paths:
        for name, expected in manifest["libraries"].items():
            if cancelled.is_set():
                raise InterruptedError("Requirements check cancelled")
            if name in found:
                continue
            path = directory / name
            if path in checked:
                continue
            checked.add(path)
            try:
                if not path.is_file():
                    continue
                expected_size = manifest.get("library_sizes", {}).get(name)
                if expected_size is not None and path.stat().st_size != expected_size:
                    continue
                report(f"Checking installed runtime: {name}")
                if digest(path) == expected:
                    found[name] = path
            except OSError:
                continue
    missing = [name for name in manifest["libraries"] if name not in found]
    report(f"Requirements: {len(found)}/{len(manifest['libraries'])} exact matches; "
           + ("no runtime download needed." if not missing else f"{len(missing)} missing or different."))
    return found, missing


class RemoteZip(io.RawIOBase):
    """Seekable HTTPS source, 4 MiB read-ahead and strict range/size validation.

    Only missing ZIP members are read. Each extracted library is authenticated
    against the SHA-256 manifest embedded in the installer.
    """
    BLOCK = 4 * 1024**2
    MAX_READ = 16 * 1024**2
    MAX_ARCHIVE = 4 * 1024**3

    def __init__(self, url, cancelled, report, opener=None):
        super().__init__()
        self.url, self.cancelled, self.report = url, cancelled, report
        self.opener = opener or urllib.request.urlopen
        self.position = 0
        self.cache_start, self.cache = 0, b""
        self.transferred = 0
        request = urllib.request.Request(url, headers={"Range": "bytes=0-0", "Accept-Encoding": "identity",
                                                       "User-Agent": "Mozilla/5.0 GPU-Link-Setup/1.0.1"})
        with self.opener(request, timeout=60) as response:
            header = response.headers.get("Content-Range", "")
            if response.status != 206 or not header.startswith("bytes 0-0/"):
                raise OSError("Server does not support selective downloads; choose a local runtime folder or wheel.")
            self.size = int(header.split("/")[-1])
            if not 1 <= self.size <= self.MAX_ARCHIVE or response.read(2) != b"P":
                raise ValueError("Invalid runtime archive response")

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        position = offset + (0 if whence == 0 else self.position if whence == 1 else self.size)
        if not 0 <= position <= self.size:
            raise ValueError("Invalid ZIP seek")
        self.position = position
        return position

    def read(self, size=-1):
        if size < 0:
            size = self.size - self.position
        if size > self.MAX_READ:
            raise ValueError("Runtime ZIP metadata request too large")
        size = min(size, self.size - self.position)
        result = bytearray()
        while len(result) < size:
            if self.cancelled.is_set():
                raise InterruptedError("Runtime download cancelled")
            if not self.cache_start <= self.position < self.cache_start + len(self.cache):
                start = self.position
                end = min(self.size, start + max(self.BLOCK, size - len(result))) - 1
                request = urllib.request.Request(self.url, headers={"Range": f"bytes={start}-{end}",
                    "Accept-Encoding": "identity", "User-Agent": "Mozilla/5.0 GPU-Link-Setup/1.0.1"})
                with self.opener(request, timeout=60) as response:
                    if response.status != 206 or response.headers.get("Content-Range") != f"bytes {start}-{end}/{self.size}":
                        raise ValueError("Unexpected runtime range response")
                    data = response.read(end - start + 2)
                    if len(data) != end - start + 1:
                        raise ValueError("Incomplete runtime download")
                self.cache_start, self.cache = start, data
                self.transferred += len(data)
                self.report(f"Downloading missing runtime data: {self.transferred / 1e6:.1f} MB")
            offset = self.position - self.cache_start
            chunk = self.cache[offset:offset + size - len(result)]
            result.extend(chunk)
            self.position += len(chunk)
        return bytes(result)

"""Worker-owned resource budgets and read-only system telemetry."""

import os
import platform
import threading
import time


import psutil

MIB = 1024**2


class Resources:
    def __init__(self, threads=None, ram_mib=256):
        self.logical = psutil.cpu_count() or 1
        self.threads = max(1, self.logical // 2) if threads is None else threads
        if type(self.threads) is not int or not 1 <= self.threads <= self.logical:
            raise ValueError("CPU thread budget exceeds worker logical processors")
        if type(ram_mib) is not int or not 16 <= ram_mib <= 4096:
            raise ValueError("RAM test budget must be 16..4096 MiB")
        self.ram_mib = ram_mib
        self.lock = threading.Lock()
        self.previous = psutil.cpu_times(percpu=True)
        self.at = time.monotonic()
        self.usage = None

    def telemetry(self):
        with self.lock:
            now = time.monotonic()
            if now - self.at >= 0.5:
                current = psutil.cpu_times(percpu=True)
                values = []
                for before, after in zip(self.previous, current):
                    total = (after.user + after.system + after.idle) - (before.user + before.system + before.idle)
                    idle = after.idle - before.idle
                    values.append(max(0, min(100, 100 * (1 - idle / total))) if total > 0 else 0)
                self.usage = sum(values) / len(values) if values else None
                self.previous, self.at = current, now
            usage = self.usage
        memory = psutil.virtual_memory()
        disks = []
        for part in psutil.disk_partitions(all=False):
            if 'cdrom' in part.opts or not part.fstype:
                continue
            try:
                disk = psutil.disk_usage(part.mountpoint)
                disks.append({"mount": part.mountpoint, "total": disk.total, "free": disk.free})
            except (OSError, PermissionError):
                continue
        io = psutil.disk_io_counters()
        return {"cpu_name": os.environ.get("PROCESSOR_IDENTIFIER") or platform.processor() or "CPU",
                "logical_processors": self.logical, "physical_cores": psutil.cpu_count(logical=False),
                "cpu_percent": usage, "ram_total": memory.total, "ram_available": memory.available,
                "ram_used": memory.total - memory.available, "ram_percent": memory.percent,
                "cpu_threads_allowed": self.threads, "ram_test_mib_allowed": self.ram_mib,
                "disks": disks, "disk_read_bytes": io.read_bytes if io else None,
                "disk_write_bytes": io.write_bytes if io else None,
                "app_rss": psutil.Process().memory_info().rss,
                "storage_access": "Inventory only; no remote file access"}

    def check_ram(self, mib):
        if type(mib) is not int or not 16 <= mib <= self.ram_mib:
            raise ValueError("Requested RAM exceeds worker RAM test budget")
        # Preserve at least 1 GiB and half of currently available system memory.
        available = psutil.virtual_memory().available
        if mib * MIB > min(available // 2, available - 1024 * MIB):
            raise RuntimeError("Insufficient available RAM while preserving worker headroom")

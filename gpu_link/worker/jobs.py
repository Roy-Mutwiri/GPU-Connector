"""Single CPU/RAM/GPU job queue, bounded retention, cancellation and controller lease."""

import threading
import time
import uuid

from gpu_link.benchmarks import compute, memory_test, stress
from gpu_link.benchmarks.cpu import compute_cpu, test_ram
from gpu_link.worker.resources import Resources


class Jobs:
    def __init__(self, gpu, resources=None):
        self.gpu = gpu
        self.resources = resources or Resources()
        self.cpu_passed = False
        self.ram_passed = False
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.current = None
        self.completed = 0
        self.compute_passed = False
        self.lease = time.monotonic()

    def submit(self, operation, args):
        if operation not in {"run_compute_test", "run_memory_test", "run_stress_test", "run_cpu_compute", "run_ram_test"}:
            raise ValueError("Job operation not allowed")
        if operation in ("run_compute_test", "run_cpu_compute") and (type(args.get("seed")) is not int or
                not 0 <= args["seed"] < 2**32):
            raise ValueError("seed must be an unsigned 32-bit integer")
        if operation == "run_stress_test" and (type(args.get("seconds")) is not int or
                args["seconds"] not in (30, 60, 300)):
            raise ValueError("Stress duration must be 30, 60 or 300 seconds")
        if operation == "run_cpu_compute":
            if type(args.get("threads")) is not int or not 1 <= args["threads"] <= self.resources.threads:
                raise ValueError("Requested CPU threads exceed worker budget")
        if operation == "run_ram_test":
            self.resources.check_ram(args.get("mib"))
        args = dict(args)
        with self.lock:
            if self.current and self.current["status"] == "running":
                raise ValueError("Worker busy")
            self.cancel.clear()
            self.lease = time.monotonic()
            self.current = {"id": uuid.uuid4().hex, "operation": operation, "status": "running",
                            "started": time.time(), "result": None, "error": None}
            job_id = self.current["id"]
        threading.Thread(target=self._run, args=(operation, args), daemon=True).start()
        return {"job_id": job_id}

    def _run(self, operation, args):
        deadline = time.monotonic() + (args.get("seconds", 0) + 30 if operation == "run_stress_test" else 60)
        done = threading.Event()
        def watchdog():
            while not done.wait(0.5):
                if time.monotonic() > deadline or time.monotonic() - self.lease > 15:
                    self.cancel.set()
        threading.Thread(target=watchdog, daemon=True).start()
        try:
            if operation == "run_compute_test":
                result = compute(args["seed"])
            elif operation == "run_memory_test":
                result = memory_test()
            elif operation == "run_cpu_compute":
                result = compute_cpu(args["seed"], args["threads"], self.cancel)
            elif operation == "run_ram_test":
                result = test_ram(args["mib"], self.cancel, self.resources)
            else:
                result = stress(args["seconds"], self.cancel, self.gpu)
            with self.lock:
                cancelled = self.cancel.is_set() or result.get("cancelled", False)
                self.current.update(status="cancelled" if cancelled else "completed", result=result)
                if not cancelled:
                    self.completed += 1
                    if operation == "run_compute_test":
                        self.compute_passed = True
                    elif operation == "run_cpu_compute":
                        self.cpu_passed = True
                    elif operation == "run_ram_test":
                        self.ram_passed = True
        except Exception as exc:
            with self.lock:
                self.current.update(status="cancelled" if self.cancel.is_set() else "failed", error=str(exc)[:500])
        finally:
            done.set()

    def get(self, job_id):
        with self.lock:
            if not self.current or self.current["id"] != job_id:
                raise ValueError("Unknown or expired job")
            self.lease = time.monotonic()
            return dict(self.current)

    def summary(self):
        with self.lock:
            return {"cpu_job_completed": self.cpu_passed, "ram_test_passed": self.ram_passed, "jobs_completed": self.completed, "compute_passed": self.compute_passed, "current_job":
                    {k: v for k, v in self.current.items() if k != "result"} if self.current else None}

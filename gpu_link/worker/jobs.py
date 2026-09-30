"""Single GPU job queue, bounded retention, cancellation and controller lease."""

import threading
import time
import uuid

from gpu_link.benchmarks import compute, memory_test, stress


class Jobs:
    def __init__(self, gpu):
        self.gpu = gpu
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.current = None
        self.completed = 0
        self.compute_passed = False
        self.lease = time.monotonic()

    def submit(self, operation, args):
        if operation == "run_compute_test" and (type(args.get("seed")) is not int or
                not 0 <= args["seed"] < 2**32):
            raise ValueError("seed must be an unsigned 32-bit integer")
        if operation == "run_stress_test" and (type(args.get("seconds")) is not int or
                args["seconds"] not in (30, 60, 300)):
            raise ValueError("Stress duration must be 30, 60 or 300 seconds")
        with self.lock:
            if self.current and self.current["status"] == "running":
                raise ValueError("GPU worker busy")
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
            else:
                result = stress(args["seconds"], self.cancel, self.gpu)
            with self.lock:
                cancelled = self.cancel.is_set() or result.get("cancelled", False)
                self.current.update(status="cancelled" if cancelled else "completed", result=result)
                if not cancelled:
                    self.completed += 1
                    if operation == "run_compute_test":
                        self.compute_passed = True
        except Exception as exc:
            with self.lock:
                self.current.update(status="failed", error=str(exc)[:500])
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
            return {"jobs_completed": self.completed, "compute_passed": self.compute_passed, "current_job":
                    {k: v for k, v in self.current.items() if k != "result"} if self.current else None}

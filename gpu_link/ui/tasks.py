"""Long-lived service thread and finite tasks; no networking on the GUI thread."""

import queue
import threading
import time

from PySide6.QtCore import QThread, Signal

from gpu_link.controller import Client, backoff
from gpu_link.controller.health import full_test, network_test
from gpu_link.diagnostics import diagnose
from gpu_link.gpu import GPU
from gpu_link.network import discover
from gpu_link.worker import Worker


class Engine(QThread):
    event = Signal(str, object)

    def __init__(self):
        super().__init__()
        self.commands = queue.Queue()
        self.stop_event = threading.Event()
        self.cancel_test = threading.Event()
        self.worker = None
        self.client = None
        self.credentials = None
        self.connected = False
        self.retry_at = 0
        self.attempt = 0
        self.ever_connected = False
        self.gpu = None

    def submit(self, op, **args):
        self.commands.put((op, args))

    def make_client(self):
        return Client(**self.credentials)

    def run(self):
        self.gpu = GPU()
        self.event.emit("local", self.gpu.info())
        last = 0
        while not self.stop_event.is_set():
            try:
                op, args = self.commands.get(timeout=0.1)
                self.command(op, args)
            except queue.Empty:
                pass
            except Exception as exc:
                self.event.emit("error", str(exc))
            now = time.monotonic()
            if now - last >= 1:
                last = now
                self.event.emit("local_telemetry", self.gpu.telemetry())
                if self.worker:
                    self.event.emit("worker_telemetry", self.worker.dispatch("get_telemetry", {}))
                if self.credentials and now >= self.retry_at:
                    try:
                        if not self.connected:
                            self.client = self.make_client()
                            self.client.connect()
                            self.event.emit("status", "RECONNECTED" if self.ever_connected else "CONNECTED")
                            self.connected = self.ever_connected = True
                            self.attempt = 0
                            self.event.emit("remote", self.client.request("get_gpu_info"))
                            self.event.emit("remote_system", self.client.request("get_system_info"))
                        started = time.perf_counter()
                        self.client.request("heartbeat")
                        self.event.emit("latency", (time.perf_counter() - started) * 1000)
                        self.event.emit("remote_telemetry", self.client.request("get_telemetry"))
                    except Exception as exc:
                        self.connected = False
                        if self.client:
                            self.client.close()
                        delay = backoff(self.attempt)
                        self.retry_at = now + delay
                        self.attempt += 1
                        self.event.emit("status", f"CONNECTION LOST — retry in {delay}s")
                        if self.attempt == 1:
                            self.event.emit("error", str(exc))
        if self.client:
            self.client.close()
        if self.worker:
            self.worker.stop()

    def command(self, op, args):
        if op == "connect":
            if self.client:
                self.client.close()
            self.credentials = args
            self.connected = self.ever_connected = False
            self.retry_at = self.attempt = 0
            self.event.emit("status", "CONNECTING")
        elif op == "disconnect":
            self.credentials = None
            self.connected = False
            if self.client:
                self.client.close()
            self.event.emit("status", "DISCONNECTED")
        elif op == "discover":
            self.event.emit("log", "Searching LAN…")
            self.event.emit("discovered", discover())
        elif op == "start_worker":
            if self.worker:
                self.worker.stop()
                self.worker = None
            worker = Worker(gpu=self.gpu, **args)
            try:
                worker.start()
            except Exception:
                worker.stop()
                raise
            self.worker = worker
            self.event.emit("worker_started", {"host": worker.host, "port": worker.port,
                "fingerprint": worker.identity.fingerprint,
                "invitation": worker.identity.invitation(worker.host, worker.port)})
        elif op == "stop_worker":
            if self.worker:
                self.worker.stop()
                self.worker = None
                self.event.emit("status", "WORKER STOPPED")
        elif op == "diagnostics":
            self.event.emit("diagnostics", diagnose(self.gpu, self.worker, self.connected))


class TestTask(QThread):
    event = Signal(str, object)

    def __init__(self, credentials, kind, seconds=30):
        super().__init__()
        self.credentials, self.kind, self.seconds = credentials, kind, seconds
        self.cancel = threading.Event()

    def run(self):
        client = Client(**self.credentials)
        def stage(name, value):
            self.event.emit("stage", (name, value))
        def progress(value):
            self.event.emit("remote_telemetry", value)
        try:
            if self.kind == "full":
                result = full_test(client, stage, self.cancel, progress)
                self.event.emit("full_result", result)
            else:
                client.connect()
                if self.kind == "network":
                    self.event.emit("network_result", network_test(client, stage, self.cancel))
                else:
                    result = client.job("run_stress_test", self.cancel, progress, seconds=self.seconds)
                    self.event.emit("stress_result", result)
        except Exception as exc:
            self.event.emit("test_error", str(exc))
        finally:
            client.close()
            self.event.emit("test_done", None)

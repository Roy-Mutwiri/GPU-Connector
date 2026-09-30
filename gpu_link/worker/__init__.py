"""LAN-only TLS worker with explicit allowed operations."""

import logging
import platform
import socket
import threading
import time
from collections import defaultdict, deque

from gpu_link.gpu import GPU
from gpu_link.network import DiscoveryResponder
from gpu_link.protocol import (OPERATIONS, VERSION, MeteredSocket, binary_size, receive_buffer, recv_json,
                               send_buffer, send_json)
from gpu_link.security import Identity, lan_address, token_matches
from gpu_link.worker.jobs import Jobs
from gpu_link.worker.resources import Resources

LOG = logging.getLogger("gpu_link")


class Worker:
    def __init__(self, host, port=8765, identity=None, gpu=None, discovery=True, allow_loopback=False, cpu_threads=None, ram_mib=256):
        if not lan_address(host, allow_loopback):
            raise ValueError("Select a private LAN IPv4 interface")
        self.host, self.port = host, port
        self.allow_loopback = allow_loopback
        self.identity = identity or Identity()
        self.gpu = gpu or GPU()
        self.resources = Resources(cpu_threads, ram_mib)
        self.jobs = Jobs(self.gpu, self.resources)
        self.discovery_enabled = discovery
        self.responder = None
        self.stopped = threading.Event()
        self.slots = threading.BoundedSemaphore(8)
        self.peers = set()
        self.lock = threading.Lock()
        self.attempts = defaultdict(deque)
        self.started = time.monotonic()
        self.info = {}
        self.sock = None
        self.network_bytes = 0

    def start(self):
        self.info = self.gpu.info()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        self.sock.bind((self.host, self.port))
        self.port = self.sock.getsockname()[1]
        self.sock.listen(8)
        self.sock.settimeout(0.5)
        threading.Thread(target=self._accept, daemon=True).start()
        if self.discovery_enabled:
            gpu = next(iter(self.info.get("gpus", [])), {})
            try:
                self.responder = DiscoveryResponder(self.host, self.port, gpu.get("name"), gpu.get("total"))
            except OSError:
                LOG.warning("Discovery unavailable; manual connection remains available")
        LOG.info("Worker listening on %s:%s with pinned TLS", self.host, self.port)

    def _accept(self):
        while not self.stopped.is_set():
            try:
                raw, addr = self.sock.accept()
            except (OSError, TimeoutError):
                continue
            now = time.monotonic()
            history = self.attempts[addr[0]]
            while history and now - history[0] > 60:
                history.popleft()
            if len(self.attempts) > 512:
                self.attempts.clear()
            if not lan_address(addr[0], self.allow_loopback) or len(history) >= 20 or not self.slots.acquire(False):
                raw.close()
                continue
            history.append(now)
            threading.Thread(target=self._serve, args=(raw,), daemon=True).start()

    def _serve(self, raw):
        sock = raw
        try:
            raw.settimeout(5)
            sock = MeteredSocket(self.identity.context.wrap_socket(raw, server_side=True), self._record_bytes)
            with self.lock:
                self.peers.add(sock)
            hello = recv_json(sock)
            if hello != {"op": "hello", "protocol": VERSION}:
                raise ValueError("Incompatible protocol")
            send_json(sock, {"ok": True, "protocol": VERSION})
            auth = recv_json(sock)
            if auth.get("op") != "authenticate" or not token_matches(self.identity.token, auth.get("token")):
                send_json(sock, {"ok": False, "error": "Authentication failed"})
                return
            send_json(sock, {"ok": True})
            calls = deque()
            while not self.stopped.is_set():
                request = recv_json(sock)
                now = time.monotonic()
                while calls and now - calls[0] > 1:
                    calls.popleft()
                if len(calls) >= 30:
                    raise ValueError("Request rate exceeded")
                calls.append(now)
                op = request.get("op")
                args = request.get("args", {})
                if op not in OPERATIONS or not isinstance(args, dict):
                    raise ValueError("Operation not allowed")
                if op in ("network_upload_test", "network_download_test"):
                    size = binary_size(args.get("size"))
                    send_json(sock, {"ok": True, "ready": size})
                    started = time.perf_counter()
                    if op == "network_upload_test":
                        receive_buffer(sock, size)
                    else:
                        send_buffer(sock, size)
                    send_json(sock, {"ok": True, "bytes": size, "seconds": time.perf_counter() - started})
                else:
                    try:
                        data = self.dispatch(op, args)
                        send_json(sock, {"ok": True, "data": data})
                    except (RuntimeError, ValueError) as exc:
                        send_json(sock, {"ok": False, "error": str(exc)[:500]})
        except Exception:
            LOG.debug("Worker client session closed")
        finally:
            with self.lock:
                self.peers.discard(sock)
            sock.close()
            raw.close()
            self.slots.release()

    def _record_bytes(self, size):
        with self.lock:
            self.network_bytes += size

    def dispatch(self, op, args):
        if op == "heartbeat":
            return {"uptime": time.monotonic() - self.started, **self.jobs.summary()}
        if op == "get_system_info":
            return {"hostname": socket.gethostname(), "os": platform.platform(), "python": platform.python_version(),
                    "ip": self.host, "port": self.port, "protocol": VERSION,
                    "capabilities": sorted(OPERATIONS)}
        if op == "get_gpu_info":
            return {**self.info, **self.gpu.telemetry()}
        if op == "get_telemetry":
            return {**self.gpu.telemetry(), **self.jobs.summary(), "resources": self.resources.telemetry(), "network_bytes": self.network_bytes,
                    "uptime": time.monotonic() - self.started}
        if op in ("run_compute_test", "run_memory_test", "run_stress_test", "run_cpu_compute", "run_ram_test"):
            return self.jobs.submit(op, args)
        if op == "get_job":
            return self.jobs.get(args.get("job_id"))
        if op == "stop_job":
            job = self.jobs.get(args.get("job_id"))
            self.jobs.cancel.set()
            return {"cancel_requested": True, "job_id": job["id"]}
        raise ValueError("Operation not allowed")

    def stop(self):
        self.stopped.set()
        self.jobs.cancel.set()
        if self.responder:
            self.responder.stop()
        if self.sock:
            self.sock.close()
        with self.lock:
            for peer in self.peers:
                try:
                    peer.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                peer.close()

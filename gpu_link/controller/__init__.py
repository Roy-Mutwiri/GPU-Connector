"""Authenticated client; reconnect is managed outside the GUI thread."""

import socket
import ssl
import threading
import time

from gpu_link.protocol import VERSION, receive_buffer, recv_json, send_buffer, send_json
from gpu_link.security import check_certificate, lan_address


class Client:
    def __init__(self, host, port, token, fingerprint, allow_loopback=False):
        if not lan_address(host, allow_loopback):
            raise ValueError("Only private LAN IPv4 addresses are supported")
        if not 1 <= int(port) <= 65535:
            raise ValueError("Invalid worker port")
        self.host, self.port, self.token, self.fingerprint = host, int(port), token, fingerprint
        self.sock = None
        self.lock = threading.RLock()

    def connect(self, stage=None):
        emit = stage or (lambda *args: None)
        with self.lock:
            self.close()
            raw = socket.create_connection((self.host, self.port), timeout=5)
            emit("TCP connection", "PASS")
            try:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE  # Exact certificate pin checked BEFORE any credentials.
                ctx.minimum_version = ssl.TLSVersion.TLSv1_2
                self.sock = ctx.wrap_socket(raw, server_hostname=self.host)
                check_certificate(self.sock, self.fingerprint)
                send_json(self.sock, {"op": "hello", "protocol": VERSION})
                hello = recv_json(self.sock)
                if hello.get("protocol") != VERSION or not hello.get("ok"):
                    raise ValueError("Incompatible worker protocol")
                emit("API/protocol handshake", "PASS")
                send_json(self.sock, {"op": "authenticate", "token": self.token})
                self._response()
                emit("Authentication", "PASS")
            except Exception:
                self.close()
                raw.close()
                raise

    def _response(self):
        response = recv_json(self.sock)
        if not response.get("ok"):
            raise RuntimeError(response.get("error", "Worker request failed"))
        return response

    def request(self, op, **args):
        with self.lock:
            if self.sock is None:
                raise ConnectionError("Not connected")
            send_json(self.sock, {"op": op, "args": args})
            return self._response()["data"]

    def bandwidth(self, direction, size):
        with self.lock:
            send_json(self.sock, {"op": f"network_{direction}_test", "args": {"size": size}})
            ready = self._response()
            if ready.get("ready") != size:
                raise ValueError("Unexpected benchmark framing")
            started = time.perf_counter()
            (send_buffer if direction == "upload" else receive_buffer)(self.sock, size)
            response = self._response()
            if response.get("bytes") != size:
                raise ValueError("Incomplete benchmark")
            elapsed = time.perf_counter() - started
            return {"bytes": size, "seconds": elapsed, "mbps": size * 8 / elapsed / 1e6,
                    "MB_s": size / elapsed / 1e6}

    def job(self, operation, cancel=None, progress=None, **args):
        job_id = self.request(operation, **args)["job_id"]
        deadline = time.monotonic() + args.get("seconds", 0) + 70
        try:
            while time.monotonic() < deadline:
                if cancel and cancel.is_set():
                    self.request("stop_job", job_id=job_id)
                    raise InterruptedError("Test cancellation requested")
                job = self.request("get_job", job_id=job_id)
                if job["status"] == "completed":
                    return job["result"]
                if job["status"] in ("failed", "cancelled"):
                    raise RuntimeError(job.get("error") or "Worker job cancelled")
                if progress:
                    progress(self.request("get_telemetry"))
                time.sleep(0.3)
            raise TimeoutError("Remote job deadline exceeded")
        except Exception:
            try:
                self.request("stop_job", job_id=job_id)
            except Exception:
                pass
            raise

    def close(self):
        with self.lock:
            if self.sock:
                try:
                    self.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.sock.close()
                self.sock = None


def backoff(attempt):
    return min(30, 2 ** min(attempt, 5))

"""Bounded wire protocol; JSON control frames and streamed binary payloads."""

import json
import struct
import time

VERSION = "GPU-LINK/1"
MAX_JSON = 1024 * 1024
MAX_BINARY = 256 * 1024 * 1024
CHUNK = 256 * 1024
OPERATIONS = frozenset({"heartbeat", "get_system_info", "get_gpu_info", "get_telemetry",
    "run_compute_test", "run_memory_test", "run_stress_test", "get_job", "stop_job",
    "network_upload_test", "network_download_test"})


class ProtocolError(ValueError):
    pass


class MeteredSocket:
    """Count application bytes without retaining their contents (TLS wire overhead excluded)."""

    def __init__(self, sock, record):
        self.sock, self.record = sock, record

    def __getattr__(self, name):
        return getattr(self.sock, name)

    def recv(self, size):
        data = self.sock.recv(size)
        self.record(len(data))
        return data

    def sendall(self, data):
        self.sock.sendall(data)
        self.record(len(data))


def recv_exact(sock, size, deadline=None):
    data = bytearray()
    while len(data) < size:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Frame deadline exceeded")
            sock.settimeout(min(remaining, 10))
        chunk = sock.recv(min(size - len(data), CHUNK))
        if not chunk:
            raise ConnectionError("Peer disconnected")
        data.extend(chunk)
    return bytes(data)


def send_json(sock, obj):
    raw = json.dumps(obj, allow_nan=False, separators=(",", ":")).encode()
    if len(raw) > MAX_JSON:
        raise ProtocolError("Control message too large")
    sock.sendall(struct.pack("!I", len(raw)) + raw)


def recv_json(sock):
    deadline = time.monotonic() + 15
    size = struct.unpack("!I", recv_exact(sock, 4, deadline))[0]
    if not 1 <= size <= MAX_JSON:
        raise ProtocolError("Invalid control frame size")
    try:
        obj = json.loads(recv_exact(sock, size, deadline), parse_constant=lambda _: None)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ProtocolError("Malformed JSON") from exc
    if not isinstance(obj, dict):
        raise ProtocolError("Expected JSON object")
    return obj


def binary_size(value):
    if type(value) is not int or not 1 <= value <= MAX_BINARY:
        raise ProtocolError("Binary size outside 1..256 MiB")
    return value


def send_buffer(sock, size):
    binary_size(size)
    block = b"G" * CHUNK
    sock.settimeout(30)
    deadline = time.monotonic() + 120
    for offset in range(0, size, CHUNK):
        if time.monotonic() > deadline:
            raise TimeoutError("Transfer deadline exceeded")
        sock.sendall(block[:min(CHUNK, size - offset)])


def receive_buffer(sock, size):
    binary_size(size)
    deadline = time.monotonic() + 120
    for offset in range(0, size, CHUNK):
        data = recv_exact(sock, min(CHUNK, size - offset), deadline)
        if data != b"G" * len(data):
            raise ProtocolError("Benchmark payload corrupted")

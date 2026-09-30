import json
import socket
import struct
import threading
import time

import pytest

from gpu_link.protocol import (MAX_JSON, MeteredSocket, ProtocolError, binary_size, receive_buffer, recv_exact,
                               recv_json, send_buffer, send_json)
from gpu_link.security import lan_address, parse_invitation, token_matches


def test_frame_roundtrip():
    a, b = socket.socketpair()
    with a, b:
        send_json(a, {"op": "heartbeat", "args": {}})
        assert recv_json(b) == {"op": "heartbeat", "args": {}}


@pytest.mark.parametrize("payload", [b"garbage", b"[]", b"null", b"\xff"])
def test_malformed(payload):
    a, b = socket.socketpair()
    with a, b:
        a.sendall(struct.pack("!I", len(payload)) + payload)
        with pytest.raises(ProtocolError):
            recv_json(b)


@pytest.mark.parametrize("size", [0, MAX_JSON + 1, 2**32 - 1])
def test_oversized_frame(size):
    a, b = socket.socketpair()
    with a, b:
        a.sendall(struct.pack("!I", size))
        with pytest.raises(ProtocolError):
            recv_json(b)


def test_partial_frame_disconnect():
    a, b = socket.socketpair()
    a.sendall(b"\x00\x00")
    a.close()
    with b, pytest.raises(ConnectionError):
        recv_json(b)


def test_deadline():
    a, b = socket.socketpair()
    with a, b, pytest.raises(TimeoutError):
        recv_exact(b, 1, time.monotonic() + 0.02)


def test_fragmented_frame():
    a, b = socket.socketpair()
    payload = json.dumps({"ok": True}).encode()
    raw = struct.pack("!I", len(payload)) + payload
    def sender():
        for value in raw:
            a.sendall(bytes([value]))
    thread = threading.Thread(target=sender)
    with a, b:
        thread.start()
        assert recv_json(b)["ok"]
        thread.join()


@pytest.mark.parametrize("value", [-1, 0, 256 * 1024**2 + 1, True, "1", None])
def test_binary_limits(value):
    with pytest.raises(ProtocolError):
        binary_size(value)


def test_memory_transfer():
    a, b = socket.socketpair()
    with a, b:
        thread = threading.Thread(target=send_buffer, args=(a, 1024**2))
        thread.start()
        receive_buffer(b, 1024**2)
        thread.join()


def test_corrupted_binary():
    a, b = socket.socketpair()
    with a, b:
        a.sendall(b"X")
        with pytest.raises(ProtocolError):
            receive_buffer(b, 1)


@pytest.mark.parametrize("wrong", [None, 123, [], "wrong", "x" * 129, "é"])
def test_bad_tokens(wrong):
    assert not token_matches("secret", wrong)


def test_good_token():
    assert token_matches("secret", "secret")


@pytest.mark.parametrize("address", ["0.0.0.0", "8.8.8.8", "127.0.0.1", "::1", "example.com"])
def test_public_bind_rejected(address):
    assert not lan_address(address)


def test_lan_addresses():
    assert lan_address("192.168.1.2")
    assert lan_address("10.0.0.2")
    assert lan_address("172.16.0.2")


def test_bad_invitation():
    with pytest.raises(ValueError):
        parse_invitation("not-a-credential")


def test_byte_meter():
    a, b = socket.socketpair()
    counts = []
    with a, b:
        metered = MeteredSocket(a, counts.append)
        metered.sendall(b"abc")
        assert b.recv(3) == b"abc"
        b.sendall(b"xy")
        assert metered.recv(2) == b"xy"
        assert counts == [3, 2]

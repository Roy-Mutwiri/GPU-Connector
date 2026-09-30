"""Explicit CPU jobs. These never substitute for CUDA verification."""
import hashlib
import hmac
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

MIB = 1024**2
ROUNDS = 20
ITERATIONS = 10_000


def lane(seed, index, cancel=None):
    value = seed.to_bytes(4, "big") + index.to_bytes(4, "big")
    salt = b"GPU-Link CPU verification v1"
    for _ in range(ROUNDS):
        if cancel is not None and cancel.is_set():
            raise InterruptedError("CPU job cancelled")
        value = hashlib.pbkdf2_hmac("sha256", value, salt, ITERATIONS)
    return value.hex()


def compute_cpu(seed, threads, cancel):
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=threads, thread_name_prefix="gpu-link-cpu") as pool:
        results = list(pool.map(lambda index: lane(seed, index, cancel), range(threads)))
    return {"backend": "cpu", "algorithm": "pbkdf2-sha256-challenge-v1", "seed": seed,
            "threads": threads, "results": results, "seconds": time.perf_counter() - started,
            "rounds": ROUNDS, "iterations_per_round": ITERATIONS}


def verify_cpu(result, seed, threads, cancel=None):
    try:
        if (result["backend"] != "cpu" or result["algorithm"] != "pbkdf2-sha256-challenge-v1"
                or result["seed"] != seed or result["threads"] != threads
                or result["rounds"] != ROUNDS or result["iterations_per_round"] != ITERATIONS
                or not isinstance(result["results"], list) or len(result["results"]) != threads):
            return False
        return all(isinstance(actual, str) and hmac.compare_digest(actual, lane(seed, i, cancel))
                   for i, actual in enumerate(result["results"]))
    except (KeyError, ValueError, TypeError):
        return False


def test_ram(mib, cancel, resources):
    resources.check_ram(mib)
    started = time.perf_counter()
    block = np.empty(mib * MIB, dtype=np.uint8)
    for pattern in (0x55, 0xAA):
        for offset in range(0, block.size, MIB):
            if cancel.is_set():
                raise InterruptedError("RAM job cancelled")
            block[offset:offset + MIB].fill(pattern)
        for offset in range(0, block.size, MIB):
            if cancel.is_set():
                raise InterruptedError("RAM job cancelled")
            if not np.all(block[offset:offset + MIB] == pattern):
                raise RuntimeError("RAM pattern verification failed")
    return {"backend": "cpu", "passed": True, "bytes": block.size,
            "patterns": [0x55, 0xAA], "seconds": time.perf_counter() - started}

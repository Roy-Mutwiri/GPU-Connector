"""Run genuine TLS + CUDA checks from source or the frozen distribution."""

import json
import sys
import tempfile
import threading
from pathlib import Path

from gpu_link.controller import Client
from gpu_link.controller.health import full_test
from gpu_link.gpu import GPU
from gpu_link.security import Identity
from gpu_link.worker import Worker


def run():
    report = {"scope": "single-PC loopback; NOT two-PC LAN/RTX 3060 verification", "stages": {}}
    output = Path(sys.argv[sys.argv.index("--report") + 1]) if "--report" in sys.argv else Path("self-test.json")
    try:
        with tempfile.TemporaryDirectory(prefix="gpu-link-test-") as directory:
            worker = Worker("127.0.0.1", 0, Identity(directory), GPU(), False, True)
            worker.start()
            client = Client("127.0.0.1", worker.port, worker.identity.token, worker.identity.fingerprint, True)
            try:
                report["gpu"] = worker.info
                report["result"] = full_test(client, lambda name, status: report["stages"].update({name: status}),
                                             threading.Event())
                if "--stress" in sys.argv:
                    report["stress"] = client.job("run_stress_test", threading.Event(), seconds=30)
                report["passed"] = report["result"]["ready"]
            finally:
                client.close()
                worker.stop()
    except Exception as exc:
        report.update(passed=False, error=str(exc))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report.get("passed") else 1

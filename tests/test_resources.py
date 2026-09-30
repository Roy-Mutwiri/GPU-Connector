import threading
import time
from types import SimpleNamespace

import pytest

from gpu_link.benchmarks.cpu import compute_cpu, test_ram as ram_job, verify_cpu
from gpu_link.worker.jobs import Jobs
from gpu_link.worker.resources import Resources
from test_worker import NoGPU, client_for, worker  # noqa: F401


def test_cpu_result_independently_verified():
    result = compute_cpu(42, 2, threading.Event())
    assert verify_cpu(result, 42, 2)
    result['results'][0] = '0' * 64
    assert not verify_cpu(result, 42, 2)


def test_cpu_result_wrong_challenge_and_backend():
    result = compute_cpu(7, 1, threading.Event())
    assert not verify_cpu(result, 8, 1)
    assert not verify_cpu(result, 7, 2)
    result['backend'] = 'cuda:0'
    assert not verify_cpu(result, 7, 1)


def test_cpu_cancel():
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        compute_cpu(1, 2, cancel)


@pytest.mark.parametrize('op,args', [
    ('run_cpu_compute', {'seed': True, 'threads': 1}),
    ('run_cpu_compute', {'seed': 1, 'threads': 2}),
    ('run_cpu_compute', {'seed': 1, 'threads': True}),
    ('run_ram_test', {'mib': 17}), ('run_ram_test', {'mib': True}),
    ('run_python', {}),
])
def test_worker_budgets(op, args):
    jobs = Jobs(NoGPU(), Resources(1, 16))
    with pytest.raises(ValueError):
        jobs.submit(op, args)
    assert jobs.current is None


def test_ram_reserves_system_headroom(monkeypatch):
    monkeypatch.setattr('psutil.virtual_memory', lambda: SimpleNamespace(available=1024**3))
    with pytest.raises(RuntimeError, match='headroom'):
        Resources(1, 16).check_ram(16)


def test_ram_writes_and_checks():
    result = ram_job(16, threading.Event(), Resources(1, 16))
    assert result['passed'] and result['bytes'] == 16 * 1024**2
    assert result['backend'] == 'cpu'


def test_ram_cancel():
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        ram_job(16, cancel, Resources(1, 16))


def test_resources_real_telemetry():
    resources = Resources(1, 16)
    first = resources.telemetry()
    assert first['cpu_percent'] is None
    time.sleep(0.55)
    info = resources.telemetry()
    assert 0 <= info['cpu_percent'] <= 100
    assert 0 < info['ram_available'] <= info['ram_total']
    assert info['cpu_threads_allowed'] == 1
    assert info['storage_access'].startswith('Inventory only')


def test_cpu_ram_over_authenticated_protocol_without_cuda(worker):  # noqa: F811
    client = client_for(worker)
    try:
        client.connect()
        assert 'run_cpu_compute' in client.request('get_system_info')['capabilities']
        info = client.request('get_telemetry')['resources']
        assert info['ram_total'] > 0
        result = client.job('run_cpu_compute', seed=98421, threads=1)
        assert verify_cpu(result, 98421, 1)
        assert client.job('run_ram_test', mib=16)['passed']
        assert worker.jobs.cpu_passed and worker.jobs.ram_passed
        assert not worker.jobs.compute_passed
    finally:
        client.close()

@pytest.mark.parametrize('kind,event', [('cpu', 'cpu_result'), ('ram', 'ram_result')])
def test_desktop_task_runs_resource_job(worker, kind, event):  # noqa: F811
    from PySide6.QtCore import Qt
    from gpu_link.ui.tasks import TestTask
    task = TestTask({'host': '127.0.0.1', 'port': worker.port,
                     'token': worker.identity.token, 'fingerprint': worker.identity.fingerprint,
                     'allow_loopback': True}, kind)
    events = []
    task.event.connect(lambda name, value: events.append((name, value)), Qt.ConnectionType.DirectConnection)
    task.run()
    assert any(name == event for name, _ in events), events
    assert not any(name == 'test_error' for name, _ in events), events

import hashlib
import io
import json
import threading
import zipfile

import pytest

from scripts import windows_setup as setup
from scripts.runtime_requirements import RemoteZip, scan_runtime


@pytest.fixture
def payload(tmp_path, monkeypatch):
    base = tmp_path / "payload"
    base.mkdir()
    with zipfile.ZipFile(base / "core.zip", "w") as archive:
        archive.writestr("GPU-Link.exe", b"test-only application fixture")
    files = {"cudart.dll": b"correct cuda", "torch.dll": b"correct torch"}
    manifest = {"core_sha256": setup.checksum(base / "core.zip"),
                "libraries": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
                "library_sizes": {name: len(data) for name, data in files.items()}}
    (base / "setup-manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(setup, "BASE", base)
    monkeypatch.setattr(setup, "driver_status", lambda: "Unit-test driver fixture")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("scripts.runtime_requirements.candidate_roots", lambda dest, extra:
                        [dest / "_internal/torch/lib"] + ([extra] if extra else []))
    return manifest, files


def network_zip(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr("torch/lib/" + name, data)
    return buffer.getvalue()


def test_matching_local_runtime_never_downloads(tmp_path, monkeypatch, payload):
    _, files = payload
    local = tmp_path / "existing-torch"
    local.mkdir()
    for name, data in files.items():
        (local / name).write_bytes(data)
    monkeypatch.setattr(setup, "RemoteZip", lambda *a: pytest.fail("Must not download a matching runtime"))
    destination = tmp_path / "app"
    exe = setup.install(destination, lambda _: None, threading.Event(), runtime_dir=local)
    assert exe.is_file()
    for name, data in files.items():
        assert (destination / "_internal/torch/lib" / name).read_bytes() == data
        assert (local / name).read_bytes() == data


def test_existing_complete_install_is_noop(tmp_path, monkeypatch, payload):
    _, files = payload
    local = tmp_path / "existing"
    local.mkdir()
    for name, data in files.items():
        (local / name).write_bytes(data)
    dest = tmp_path / "app"
    setup.install(dest, lambda _: None, threading.Event(), runtime_dir=local)
    monkeypatch.setattr(setup, "RemoteZip", lambda *a: pytest.fail("Must stay offline"))
    before = {p: p.stat().st_mtime_ns for p in dest.rglob("*") if p.is_file()}
    messages = []
    setup.install(dest, messages.append, threading.Event())
    assert any("already up to date" in message for message in messages)
    assert before == {p: p.stat().st_mtime_ns for p in before}


def test_only_missing_runtime_is_requested(tmp_path, monkeypatch, payload):
    _, files = payload
    local = tmp_path / "partial"
    local.mkdir()
    (local / "cudart.dll").write_bytes(files["cudart.dll"])
    # The mock remote archive deliberately has ONLY the missing member.
    monkeypatch.setattr(setup, "RemoteZip", lambda *a: io.BytesIO(network_zip({"torch.dll": files["torch.dll"]})))
    setup.install(tmp_path / "app", lambda _: None, threading.Event(), runtime_dir=local)
    assert (tmp_path / "app/_internal/torch/lib/torch.dll").read_bytes() == files["torch.dll"]


def test_wrong_local_version_is_not_reused(tmp_path, payload):
    manifest, files = payload
    (tmp_path / "cudart.dll").write_bytes(b"wrong version")
    (tmp_path / "torch.dll").write_bytes(files["torch.dll"])
    found, missing = scan_runtime(manifest, tmp_path, lambda _: None, threading.Event(), candidates=[tmp_path])
    assert set(found) == {"torch.dll"}
    assert missing == ["cudart.dll"]


def test_corrupt_remote_library_not_installed(tmp_path, monkeypatch, payload):
    _, files = payload
    monkeypatch.setattr(setup, "RemoteZip", lambda *a: io.BytesIO(network_zip({**files, "cudart.dll": b"corrupt"})))
    with pytest.raises(ValueError, match="verification failed"):
        setup.install(tmp_path / "app", lambda _: None, threading.Event())
    assert not (tmp_path / "app/_internal/torch/lib/cudart.dll").exists()
    assert not list((tmp_path / "app").rglob("*.installing"))


def test_requirements_check_is_readonly(tmp_path, monkeypatch, payload):
    monkeypatch.setattr(setup, "RemoteZip", lambda *a: pytest.fail("Checking cannot download"))
    dest = tmp_path / "not-created"
    _, found, missing = setup.check_requirements(dest, lambda _: None, threading.Event())
    assert not found and len(missing) == 2 and not dest.exists()


def test_scanning_cancelled(tmp_path, payload):
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(InterruptedError):
        scan_runtime(payload[0], tmp_path, lambda _: None, cancelled, candidates=[tmp_path])


def test_remote_zip_strict_ranges():
    raw = network_zip({"torch.dll": b"verified fixture"})
    calls = []
    def opener(request, timeout):
        value = request.get_header("Range")
        start, end = (int(v) for v in value.removeprefix("bytes=").split("-"))
        calls.append((start, end))
        response = io.BytesIO(raw[start:end + 1])
        response.status = 206
        response.headers = {"Content-Range": f"bytes {start}-{end}/{len(raw)}"}
        return response
    with RemoteZip("https://example.invalid/runtime", threading.Event(), lambda _: None, opener) as remote:
        with zipfile.ZipFile(remote) as archive:
            assert archive.read("torch/lib/torch.dll") == b"verified fixture"
    assert calls[0] == (0, 0)


def test_remote_zip_rejects_non_range_server():
    def opener(*args, **kwargs):
        response = io.BytesIO(b"PK")
        response.status, response.headers = 200, {}
        return response
    with pytest.raises(OSError, match="selective"):
        RemoteZip("https://example.invalid/runtime", threading.Event(), lambda _: None, opener)

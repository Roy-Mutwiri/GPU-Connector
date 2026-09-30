import threading

import pytest

from scripts.windows_setup import install, safe_path


@pytest.mark.parametrize("name", ["../escape", "/escape", "C:/escape", "x/../../escape", "x\\escape"])
def test_installer_rejects_unsafe_paths(tmp_path, name):
    with pytest.raises(ValueError):
        safe_path(tmp_path, name)


def test_installer_safe_nested_path(tmp_path):
    assert safe_path(tmp_path, "_internal/library.dll") == tmp_path / "_internal" / "library.dll"


def test_installer_does_not_overwrite_existing_files(tmp_path):
    existing = tmp_path / "personal.txt"
    existing.write_text("keep")
    with pytest.raises(ValueError, match="empty folder"):
        install(tmp_path, lambda _: None, threading.Event())
    assert existing.read_text() == "keep"


def test_installer_rejects_bad_runtime_hash(tmp_path, monkeypatch):
    from collections import namedtuple
    disk = namedtuple("Disk", "total used free")
    monkeypatch.setattr("scripts.windows_setup.shutil.disk_usage", lambda _: disk(10**12, 0, 10**12))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    wheel = tmp_path / "wrong.whl"
    wheel.write_bytes(b"This is not a trusted runtime")
    with pytest.raises(ValueError, match="checksum mismatch"):
        install(tmp_path / "installation", lambda _: None, threading.Event(), wheel)
    assert list((tmp_path / "installation").iterdir()) == []

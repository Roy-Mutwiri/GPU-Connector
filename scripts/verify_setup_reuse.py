"""Real offline installation + real selective official-runtime download validation."""
import hashlib
import json
import sys
import threading
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import windows_setup as setup  # noqa: E402
from scripts.runtime_requirements import RemoteZip  # noqa: E402

destination = Path("artifacts/reuse-install-check").resolve()
messages = []


def report(value):
    messages.append(value)
    print(value, flush=True)


def reject_network(*args, **kwargs):
    raise AssertionError("Offline reuse attempted a network download")


original = setup.RemoteZip
setup.RemoteZip = reject_network
try:
    exe = setup.install(destination, report, threading.Event(), runtime_dir=Path(".venv").resolve())
    stamps = {p: p.stat().st_mtime_ns for p in destination.rglob("*") if p.is_file()}
    setup.install(destination, report, threading.Event())
    assert stamps == {p: p.stat().st_mtime_ns for p in stamps}, "Complete installation was rewritten"
finally:
    setup.RemoteZip = original

manifest = setup.load_manifest()
with RemoteZip(setup.WHEEL_URL, threading.Event(), report) as remote:
    with zipfile.ZipFile(remote) as archive:
        data = archive.read("torch/lib/cudart64_12.dll")
    assert hashlib.sha256(data).hexdigest() == manifest["libraries"]["cudart64_12.dll"]
    fetched = remote.transferred
result = {"offline_install": True, "existing_install_noop": True,
          "selective_download_verified": True, "selective_network_bytes": fetched,
          "cuda_runtime_bytes": len(data), "installed_exe": str(exe)}
Path("artifacts/installer-reuse.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2), flush=True)

"""Prepare the small installer payload from the tested one-directory distribution."""
import hashlib
import json
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
base = root / "dist" / "GPU-Link"
library = base / "_internal" / "torch" / "lib"
output = root / "release"
output.mkdir(exist_ok=True)
core = output / "core.zip"
with zipfile.ZipFile(core, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for path in base.rglob("*"):
        if path.is_file() and not path.is_relative_to(library):
            archive.write(path, path.relative_to(base))
with zipfile.ZipFile(core) as archive:
    assert archive.testzip() is None


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


manifest = {"core_sha256": digest(core), "libraries": {
    path.relative_to(library).as_posix(): digest(path)
    for path in library.rglob("*") if path.is_file()}}
manifest["library_sizes"] = {path.relative_to(library).as_posix(): path.stat().st_size
                            for path in library.rglob("*") if path.is_file()}
(output / "setup-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print(f"Verified core: {core.stat().st_size:,} bytes; runtime files: {len(manifest['libraries'])}")

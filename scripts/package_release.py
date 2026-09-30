"""Create and validate a portable ZIP containing the complete tested distribution."""
import hashlib
import json
import time
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = root / "dist" / "GPU-Link"
output = root / "release"
output.mkdir(exist_ok=True)
archive = output / "GPU-Link-v1.0.0-Windows-x64.zip"
started = time.monotonic()
files = sorted(p for p in source.rglob("*") if p.is_file())
with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as bundle:
    for index, path in enumerate(files):
        bundle.write(path, path.relative_to(source.parent))
        if index % 400 == 0:
            print(f"Packaged {index}/{len(files)} files", flush=True)
print(f"ZIP size: {archive.stat().st_size:,} bytes; verifying all entries", flush=True)
with zipfile.ZipFile(archive) as bundle:
    corrupt = bundle.testzip()
    if corrupt:
        raise RuntimeError(f"Archive verification failed: {corrupt}")
    assert "GPU-Link/GPU-Link.exe" in bundle.namelist()
with archive.open("rb") as handle:
    digest = hashlib.file_digest(handle, "sha256").hexdigest()
(output / "SHA256SUMS.txt").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
(output / "package.json").write_text(json.dumps({"archive": archive.name, "bytes": archive.stat().st_size,
    "sha256": digest, "files": len(files), "verified": True,
    "seconds": time.monotonic() - started}, indent=2), encoding="utf-8")
print((output / "package.json").read_text(), flush=True)

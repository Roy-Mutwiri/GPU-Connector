"""Repack verified compressed entries into independent ZIPs below GitHub's asset limit."""
import copy
import hashlib
import json
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
output = root / "release"
source = output / "GPU-Link-v1.0.0-Windows-x64.zip"
limit = 1_600_000_000
archives = []
with zipfile.ZipFile(source) as original:
    entries = original.infolist()
    groups, group, size = [], [], 0
    for index, entry in enumerate(entries):
        end = entries[index + 1].header_offset if index + 1 < len(entries) else original.start_dir
        length = end - entry.header_offset
        if size + length > limit and group:
            groups.append(group)
            group, size = [], 0
        if length > limit:
            raise RuntimeError("One entry exceeds the release partition size")
        group.append((entry, length))
        size += length
    if group:
        groups.append(group)
    for number, group in enumerate(groups, 1):
        path = output / f"GPU-Link-v1.0.0-Windows-x64-part{number}.zip"
        with zipfile.ZipFile(path, "w", allowZip64=True) as target:
            for entry, length in group:
                original.fp.seek(entry.header_offset)
                cloned = copy.copy(entry)
                cloned.header_offset = target.fp.tell()
                while length:
                    block = original.fp.read(min(length, 1024 * 1024))
                    if not block:
                        raise RuntimeError("Truncated source archive")
                    target.fp.write(block)
                    length -= len(block)
                # Preserve original compressed bytes and metadata; ZipFile writes fresh central records.
                target.filelist.append(cloned)
                target.NameToInfo[cloned.filename] = cloned
                target.start_dir = target.fp.tell()
                target._didModify = True
        with zipfile.ZipFile(path) as verified:
            assert verified.testzip() is None, "ZIP CRC verification failed"
        assert path.stat().st_size < 2 * 1024**3
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        archives.append({"name": path.name, "bytes": path.stat().st_size, "sha256": digest})
        print(f"Verified {path.name}: {path.stat().st_size:,} bytes", flush=True)
    all_names = []
    for item in archives:
        with zipfile.ZipFile(output / item["name"]) as part:
            all_names.extend(part.namelist())
    assert sorted(all_names) == sorted(original.namelist()), "Partition files do not match source"
(output / "SHA256SUMS.txt").write_text("".join(f"{a['sha256']}  {a['name']}\n" for a in archives), encoding="ascii")
(output / "release-assets.json").write_text(json.dumps(archives, indent=2), encoding="utf-8")
print("Every original file is included exactly once. All ZIP entries passed CRC validation.", flush=True)

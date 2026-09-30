"""Hash-verified installer that reuses matching local CUDA/PyTorch requirements."""
import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import zipfile
from pathlib import Path, PurePosixPath

from scripts.runtime_requirements import RemoteZip, driver_status, scan_runtime

WHEEL_URL = "https://download.pytorch.org/whl/cu128/torch-2.11.0%2Bcu128-cp311-cp311-win_amd64.whl"
WHEEL_SHA = "90ef0c2454e5296a9fb021ddd42252e4ce1abe2c0a4988a173ef90a6cded0bf5"
BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1] / "release"))


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_path(root, name):
    parts = PurePosixPath(name).parts
    if not parts or any(p in ("..", ".") or ":" in p or "\\" in p for p in parts):
        raise ValueError("Unsafe archive path")
    target = root.joinpath(*parts).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("Archive path escaped installation folder")
    return target


def load_manifest():
    manifest = json.loads((BASE / "setup-manifest.json").read_text(encoding="utf-8"))
    if checksum(BASE / "core.zip") != manifest["core_sha256"]:
        raise ValueError("Installer application payload is corrupt")
    return manifest


def core_matches(destination, cancelled):
    if not (destination / "GPU-Link.exe").is_file():
        return False
    with zipfile.ZipFile(BASE / "core.zip") as archive:
        for entry in archive.infolist():
            if cancelled.is_set():
                raise InterruptedError("Requirements check cancelled")
            path = safe_path(destination, entry.filename)
            if not path.is_file() or path.stat().st_size != entry.file_size:
                return False
            with archive.open(entry) as source:
                expected = hashlib.file_digest(source, "sha256").hexdigest()
            if checksum(path) != expected:
                return False
    return True


def check_requirements(destination, report, cancelled, runtime_dir=None):
    manifest = load_manifest()
    report("Checking GPU Link requirements against the tested PyTorch 2.11.0 / CUDA 12.8 runtime…")
    report(driver_status())
    found, missing = scan_runtime(manifest, destination, report, cancelled, runtime_dir)
    return manifest, found, missing


def install(destination, report, cancelled, wheel_override=None, runtime_dir=None):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()
    existing = core_matches(destination, cancelled) if any(destination.iterdir()) else False
    if any(destination.iterdir()) and not existing:
        raise ValueError("Choose an empty folder or this GPU Link version's existing installation; unrelated files are never overwritten.")
    manifest, found, missing = check_requirements(destination, report, cancelled, runtime_dir)
    if wheel_override:
        found, missing = {}, list(manifest["libraries"])
    library = destination / "_internal" / "torch" / "lib"
    if existing and not missing and all(path.resolve() == safe_path(library, name)
                                        for name, path in found.items()):
        report("GPU Link requirements are already up to date. No download or installation needed.")
        return destination / "GPU-Link.exe"
    if shutil.disk_usage(destination).free < 5 * 1024**3:
        raise ValueError("At least 5 GiB free disk space is required for installation.")
    cache = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "GPU Link" / "setup-cache"
    wheel = Path(wheel_override) if wheel_override else cache / "torch-2.11.0-cu128.whl"
    use_wheel = bool(missing and wheel.is_file())
    if use_wheel:
        report("Verifying existing downloaded runtime package…")
        if checksum(wheel) != WHEEL_SHA:
            if wheel_override:
                raise ValueError("Runtime checksum mismatch; no runtime code was executed.")
            report("Cached package differs from requirements; ignoring it.")
            use_wheel = False
    if wheel_override and not use_wheel:
        raise ValueError("Runtime checksum mismatch or package missing")
    if not existing:
        report("Extracting GPU Link…")
        with zipfile.ZipFile(BASE / "core.zip") as archive:
            for entry in archive.infolist():
                if cancelled.is_set():
                    raise InterruptedError("Installation cancelled")
                target = safe_path(destination, entry.filename)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
    def put_file(name, source):
        target = safe_path(library, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".installing")
        try:
            count = 0
            with temporary.open("wb") as output:
                while block := source.read(1024 * 1024):
                    if cancelled.is_set():
                        raise InterruptedError("Installation cancelled")
                    count += len(block)
                    if count > 1024**3:
                        raise ValueError("Runtime library exceeds size limit")
                    output.write(block)
            if checksum(temporary) != manifest["libraries"][name]:
                raise ValueError(f"Library verification failed: {name}")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    for name, path in found.items():
        if path.resolve() != safe_path(library, name):
            report(f"Reusing installed requirement: {name}")
            with path.open("rb") as source:
                put_file(name, source)
    remote = None
    if missing:
        report(f"Installing {len(missing)} missing/different runtime file(s)…")
        if not use_wheel:
            remote = RemoteZip(WHEEL_URL, cancelled, report)
        with zipfile.ZipFile(wheel if use_wheel else remote) as archive:
            for name in missing:
                entry = archive.getinfo("torch/lib/" + name)
                if entry.file_size > 1024**3 or entry.compress_size > 1024**3:
                    raise ValueError("Runtime ZIP member exceeds size limit")
                report(f"Installing required runtime: {name}")
                with archive.open(entry) as source:
                    put_file(name, source)
        if remote:
            remote.close()
    for name, expected in manifest["libraries"].items():
        if checksum(safe_path(library, name)) != expected:
            raise ValueError(f"Installed requirement verification failed: {name}")
    report("All requirements verified. Click Launch GPU Link.")
    return destination / "GPU-Link.exe"


def main():
    if "--install-test" in sys.argv:
        destination = sys.argv[sys.argv.index("--install-test") + 1]
        wheel = sys.argv[sys.argv.index("--wheel") + 1] if "--wheel" in sys.argv else None
        runtime = sys.argv[sys.argv.index("--runtime-dir") + 1] if "--runtime-dir" in sys.argv else None
        exe = install(destination, print, threading.Event(), wheel, runtime)
        print(f"INSTALL VERIFIED: {exe}")
        return 0
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    root = tk.Tk()
    root.title("GPU Link Setup")
    root.geometry("740x460")
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="GPU LINK", font=("Segoe UI", 24, "bold")).pack(anchor="w")
    ttk.Label(frame, text="Install the complete Windows controller and GPU worker.").pack(anchor="w", pady=8)
    ttk.Label(frame, text="Existing CUDA/PyTorch files are checked first. Matching files are reused.\n"
              "Only missing or different runtime files are downloaded. Allow 5 GiB free for a new install.\n"
              "Compatibility target: PyTorch 2.11.0 / CUDA 12.8, not the newest untested versions.").pack(anchor="w")
    driver_message = tk.StringVar(value="NVIDIA driver: checking…")
    ttk.Label(frame, textvariable=driver_message, wraplength=680).pack(anchor="w", pady=4)
    destination = tk.StringVar(value=str(Path(os.environ["LOCALAPPDATA"]) / "Programs" / "GPU Link"))
    row = ttk.Frame(frame)
    row.pack(fill="x", pady=15)
    field = ttk.Entry(row, textvariable=destination, width=68)
    field.pack(side="left", fill="x", expand=True)
    def browse():
        selected = filedialog.askdirectory(title="Choose an empty installation folder")
        if selected:
            destination.set(selected)
    chooser = ttk.Button(row, text="Browse…", command=browse)
    chooser.pack(side="right")
    ttk.Label(frame, text="Optional: existing PyTorch, CUDA, or GPU Link folder (common locations are checked automatically)").pack(anchor="w")
    runtime = tk.StringVar()
    runtime_row = ttk.Frame(frame)
    runtime_row.pack(fill="x", pady=8)
    runtime_field = ttk.Entry(runtime_row, textvariable=runtime, width=68)
    runtime_field.pack(side="left", fill="x", expand=True)
    def browse_runtime():
        selected = filedialog.askdirectory(title="Select an existing runtime or Python environment")
        if selected:
            runtime.set(selected)
    runtime_chooser = ttk.Button(runtime_row, text="Browse…", command=browse_runtime)
    runtime_chooser.pack(side="right")
    status = tk.StringVar(value="Ready to install. NVIDIA driver required for GPU computation.")
    ttk.Label(frame, textvariable=status, wraplength=635).pack(anchor="w", pady=8)
    events = queue.Queue()
    cancelled = threading.Event()
    state = {"busy": False, "exe": None}
    controls = ttk.Frame(frame)
    controls.pack(fill="x", pady=8)
    def enable_controls(enabled):
        for control in (start_button, check_button, field, chooser, runtime_field, runtime_chooser):
            control.configure(state="normal" if enabled else "disabled")
    def start(check_only=False):
        if state["busy"]:
            return
        state["busy"] = True
        cancelled.clear()
        enable_controls(False)
        target, selected_runtime = destination.get(), runtime.get() or None
        def worker():
            try:
                def notify(s):
                    events.put(("status", s))
                if check_only:
                    _, found, missing = check_requirements(target, notify, cancelled, selected_runtime)
                    events.put(("checked", f"{len(found)} requirements ready; {len(missing)} missing/different. "
                                + ("No runtime download needed." if not missing else "Install will obtain only missing/different files.")))
                else:
                    exe = install(target, notify, cancelled, runtime_dir=selected_runtime)
                    events.put(("done", exe))
            except Exception as exc:
                events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()
    def close():
        if state["busy"]:
            cancelled.set()
            status.set("Cancelling after current file operation…")
        else:
            root.destroy()
    def poll():
        try:
            while True:
                kind, value = events.get_nowait()
                if kind == "status":
                    status.set(value)
                    if value.startswith("NVIDIA") or value.startswith("Windows NVIDIA"):
                        driver_message.set(value)
                elif kind == "done":
                    state.update(busy=False, exe=value)
                    launch.configure(state="normal")
                    enable_controls(True)
                elif kind == "checked":
                    state["busy"] = False
                    status.set(value)
                    enable_controls(True)
                else:
                    state["busy"] = False
                    status.set(value)
                    enable_controls(True)
                    messagebox.showerror("GPU Link Setup", value)
        except queue.Empty:
            pass
        root.after(100, poll)
    start_button = ttk.Button(controls, text="Install GPU Link", command=start)
    start_button.pack(side="left")
    check_button = ttk.Button(controls, text="Check requirements", command=lambda: start(True))
    check_button.pack(side="left", padx=8)
    launch = ttk.Button(controls, text="Launch GPU Link", state="disabled",
                        command=lambda: subprocess.Popen([str(state["exe"])]))
    launch.pack(side="left", padx=8)
    ttk.Button(controls, text="Close / Cancel", command=close).pack(side="right")
    root.protocol("WM_DELETE_WINDOW", close)
    root.after(100, poll)
    if "--gui-smoke" in sys.argv:
        root.withdraw()
        root.after(1000, root.destroy)
    else:
        root.after(200, lambda: start(True))
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

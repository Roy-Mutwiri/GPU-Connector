"""Small signed-hash installer; the installed application remains fully offline-capable."""
import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

WHEEL_URL = "https://download-r2.pytorch.org/whl/cu128/torch-2.11.0%2Bcu128-cp311-cp311-win_amd64.whl"
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


def install(destination, report, cancelled, wheel_override=None):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError("Choose an empty folder; existing installations are never overwritten.")
    if shutil.disk_usage(destination).free < 9 * 1024**3:
        raise ValueError("At least 9 GiB free disk space is required during installation.")
    cache = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "GPU Link" / "setup-cache"
    cache.mkdir(parents=True, exist_ok=True)
    wheel = Path(wheel_override) if wheel_override else cache / "torch-2.11.0-cu128.whl"
    if not wheel_override and not wheel.exists():
        report("Downloading CUDA runtime from pytorch.org (about 2.9 GB)…")
        partial = wheel.with_suffix(".partial")
        try:
            request = urllib.request.Request(WHEEL_URL, headers={"User-Agent": "GPU-Link-Setup/1.0"})
            with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as stream:
                total = int(response.headers.get("Content-Length", 0))
                count, last = 0, 0
                while block := response.read(1024 * 1024):
                    if cancelled.is_set():
                        raise InterruptedError("Download cancelled")
                    stream.write(block)
                    count += len(block)
                    if count - last >= 8 * 1024**2:
                        report(f"Downloading runtime: {count / 1e6:.0f} MB" +
                               (f" / {total / 1e6:.0f} MB" if total else ""))
                        last = count
            partial.replace(wheel)
        finally:
            partial.unlink(missing_ok=True)
    report("Verifying official runtime SHA-256…")
    if checksum(wheel) != WHEEL_SHA:
        if not wheel_override:
            wheel.unlink(missing_ok=True)
        raise ValueError("Runtime checksum mismatch; no runtime code was executed. Retry installation.")
    manifest = json.loads((BASE / "setup-manifest.json").read_text(encoding="utf-8"))
    core = BASE / "core.zip"
    if checksum(core) != manifest["core_sha256"]:
        raise ValueError("Installer application payload is corrupt")
    report("Extracting GPU Link…")
    with zipfile.ZipFile(core) as archive:
        for entry in archive.infolist():
            if cancelled.is_set():
                raise InterruptedError("Installation cancelled; choose a new empty folder to retry.")
            target = safe_path(destination, entry.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
    report("Installing CUDA libraries…")
    library = destination / "_internal" / "torch" / "lib"
    with zipfile.ZipFile(wheel) as archive:
        for name, digest in manifest["libraries"].items():
            if cancelled.is_set():
                raise InterruptedError("Installation cancelled; choose a new empty folder to retry.")
            target = safe_path(library, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open("torch/lib/" + name) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
            if checksum(target) != digest:
                raise ValueError(f"Library verification failed: {name}")
    if not wheel_override:
        wheel.unlink(missing_ok=True)
    report("Installed and verified. Click Launch GPU Link.")
    return destination / "GPU-Link.exe"


def main():
    if "--install-test" in sys.argv:
        destination = sys.argv[sys.argv.index("--install-test") + 1]
        wheel = sys.argv[sys.argv.index("--wheel") + 1]
        exe = install(destination, print, threading.Event(), wheel)
        print(f"INSTALL VERIFIED: {exe}")
        return 0
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    root = tk.Tk()
    root.title("GPU Link Setup")
    root.geometry("690x330")
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="GPU LINK", font=("Segoe UI", 24, "bold")).pack(anchor="w")
    ttk.Label(frame, text="Install the complete Windows controller and GPU worker.").pack(anchor="w", pady=8)
    ttk.Label(frame, text="Setup downloads ~2.9 GB from PyTorch’s official server. 9 GiB free space required.\n"
              "No separate Python installation. GPU Link works offline on your LAN after setup.").pack(anchor="w")
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
    status = tk.StringVar(value="Ready to install. NVIDIA driver required for GPU computation.")
    ttk.Label(frame, textvariable=status, wraplength=635).pack(anchor="w", pady=8)
    events = queue.Queue()
    cancelled = threading.Event()
    state = {"busy": False, "exe": None}
    controls = ttk.Frame(frame)
    controls.pack(fill="x", pady=8)
    def start():
        state["busy"] = True
        start_button.configure(state="disabled")
        field.configure(state="disabled")
        chooser.configure(state="disabled")
        target = destination.get()
        def worker():
            try:
                exe = install(target, lambda s: events.put(("status", s)), cancelled)
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
                elif kind == "done":
                    state.update(busy=False, exe=value)
                    launch.configure(state="normal")
                else:
                    state["busy"] = False
                    status.set(value)
                    messagebox.showerror("GPU Link Setup", value)
        except queue.Empty:
            pass
        root.after(100, poll)
    start_button = ttk.Button(controls, text="Install GPU Link", command=start)
    start_button.pack(side="left")
    launch = ttk.Button(controls, text="Launch GPU Link", state="disabled",
                        command=lambda: subprocess.Popen([str(state["exe"])]))
    launch.pack(side="left", padx=8)
    ttk.Button(controls, text="Close / Cancel", command=close).pack(side="right")
    root.protocol("WM_DELETE_WINDOW", close)
    root.after(100, poll)
    if "--gui-smoke" in sys.argv:
        root.withdraw()
        root.after(1000, root.destroy)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""GPU Link desktop UI. Every measurement comes from service events."""

import ctypes
import json
import logging
import os
import socket
import time

from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox, QPushButton,
    QSpinBox, QStackedWidget, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget)

from gpu_link.controller.health import STAGES
from gpu_link.network import interfaces
from gpu_link.security import parse_invitation
from gpu_link.ui.charts import Trace
from gpu_link.ui.tasks import Engine, TestTask

STYLE = """
QWidget { background: #0a1220; color: #e5edf8; font-family: 'Segoe UI'; font-size: 13px; }
QMainWindow { background: #0a1220; }
QLabel#title { font-size: 29px; font-weight: 700; }
QLabel#subtitle { color: #8da4c2; }
QLabel#status { background: #173829; color: #8ce6af; padding: 12px; border-radius: 8px; font-weight: 700; }
QFrame#card { background: #111d30; border: 1px solid #25344b; border-radius: 12px; }
QFrame#card QLabel { background: transparent; }
QPushButton { background: #233954; border: 1px solid #365273; border-radius: 6px; padding: 9px 15px; }
QPushButton:hover { background: #304e70; }
QPushButton:disabled { color: #60728a; background: #162337; }
QPushButton#primary { background: #176847; border-color: #299e70; }
QLineEdit, QSpinBox, QComboBox, QTextEdit { background: #111d30; border: 1px solid #30415b; padding: 8px; border-radius: 5px; }
QListWidget { background: #0d1829; border: 0; padding: 12px; }
QListWidget::item { padding: 14px 12px; border-radius: 6px; }
QListWidget::item:selected { background: #233b58; color: #8ce6af; }
QTableWidget { background: #111d30; border: 1px solid #25344b; gridline-color: #25344b; }
QHeaderView::section { background: #1b2b43; color: #b8c9df; padding: 8px; border: 0; }
"""


def button(text, action, primary=False):
    widget = QPushButton(text)
    if primary:
        widget.setObjectName("primary")
    widget.clicked.connect(action)
    return widget


def label(text, name=None):
    widget = QLabel(text)
    widget.setWordWrap(True)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        widget.setObjectName(name)
    return widget


def gb(value):
    return "Unknown" if value is None else f"{value / 1024**3:.1f} GiB"


def gpu_text(info):
    gpu = next(iter(info.get("gpus", [])), {})
    def value(key, unit):
        v = gpu.get(key)
        return "Unavailable" if v is None else f"{v} {unit}"
    return (f"{gpu.get('name', 'No NVIDIA GPU telemetry')}\n\n"
            f"VRAM  {gb(gpu.get('total'))} total  /  {gb(gpu.get('used'))} used  /  {gb(gpu.get('free'))} free\n"
            f"Utilization  {value('utilization', '%')}     Temperature  {value('temperature', '°C')}\n"
            f"Power  {value('power', 'W')}\nDriver  {info.get('driver') or 'Unavailable'}\n"
            f"CUDA runtime  {'AVAILABLE' if info.get('runtime_available') else 'UNAVAILABLE / NOT CHECKED'}\n"
            f"PyTorch CUDA  {'AVAILABLE' if info.get('torch_cuda') else 'UNAVAILABLE / NOT CHECKED'}"
            f"   Build {info.get('cuda_build') or '—'}")


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GPU Link")
        self.resize(1240, 880)
        self.setMinimumSize(1000, 740)
        self.setStyleSheet(STYLE)
        self.settings = QSettings("GPU Link", "GPU Link")
        self.mode = self.settings.value("mode", "")
        self.local, self.remote = {}, {}
        self.credentials = None
        self.invitation = ""
        self.task = None
        self.verified = False
        self.connected = False
        self.last_network = None
        self.remote_system = ""
        self.closing = False
        self.engine = Engine()
        self.engine.event.connect(self.handle)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        sidebar = QWidget()
        sidebar.setFixedWidth(215)
        side = QVBoxLayout(sidebar)
        side.addWidget(label("GPU LINK", "title"))
        side.addWidget(label("LAN COMPUTE FABRIC", "subtitle"))
        self.nav = QListWidget()
        self.nav.addItems(["Dashboard", "Connection", "Benchmark", "Stress Test", "Diagnostics", "Logs", "Settings"])
        side.addWidget(self.nav)
        self.mode_label = label("")
        side.addWidget(self.mode_label)
        side.addWidget(label("Remote VRAM is not\nunified CUDA memory.", "subtitle"))
        layout.addWidget(sidebar)
        body = QVBoxLayout()
        body.setContentsMargins(24, 22, 24, 20)
        self.status = label("INITIALIZING — probing real hardware", "status")
        body.addWidget(self.status)
        self.pages = QStackedWidget()
        body.addWidget(self.pages)
        layout.addLayout(body)
        self.build_dashboard()
        self.build_connection()
        self.build_benchmark()
        self.build_stress()
        self.build_diagnostics()
        self.logs = QTextEdit()
        self.logs.setReadOnly(True)
        self.logs.document().setMaximumBlockCount(2000)
        self.pages.addWidget(self.logs)
        self.build_settings()
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)
        self.engine.start()
        QTimer.singleShot(100, self.first_run)

    def page(self, title, subtitle):
        widget = QWidget()
        box = QVBoxLayout(widget)
        box.setContentsMargins(0, 14, 0, 0)
        box.addWidget(label(title, "title"))
        box.addWidget(label(subtitle, "subtitle"))
        self.pages.addWidget(widget)
        return box

    def card(self, title):
        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.addWidget(label(title, "subtitle"))
        content = label("Detecting…")
        layout.addWidget(content)
        return frame, content

    def build_dashboard(self):
        page = self.page("Your compute network", "Two physical GPUs. Explicit remote jobs. Measured results.")
        grid = QGridLayout()
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(2, 1)
        local, self.local_label = self.card("LOCAL GPU")
        remote, self.remote_label = self.card("REMOTE GPU")
        self.remote_label.setText("Not connected\nOpen Connection to pair a worker.")
        grid.addWidget(local, 0, 0)
        grid.addWidget(label("↔\nLAN"), 0, 1)
        grid.addWidget(remote, 0, 2)
        network, self.network_label = self.card("NETWORK")
        compute, self.compute_label = self.card("REMOTE GPU COMPUTE")
        self.network_label.setText("Not measured")
        self.compute_label.setText("NOT TESTED\nRun the full test to verify CUDA execution.")
        grid.addWidget(network, 1, 0)
        grid.addWidget(compute, 1, 2)
        page.addLayout(grid)
        self.resources = label("AVAILABLE GPU RESOURCES — waiting for telemetry")
        page.addWidget(self.resources)
        page.addWidget(label("Remote VRAM is not unified CUDA memory. Physical total is inventory, not one allocation.", "subtitle"))
        self.worker_status = label("")
        page.addWidget(self.worker_status)
        page.addWidget(button("RUN FULL TEST", lambda: self.run_test("full"), True))
        page.addStretch()

    def build_connection(self):
        page = self.page("Connection", "Pair using connection information copied directly from your worker.")
        self.controller_box = QWidget()
        box = QVBoxLayout(self.controller_box)
        self.paste = QLineEdit()
        self.paste.setEchoMode(QLineEdit.EchoMode.Password)
        self.paste.setPlaceholderText("Paste worker connection information (contains a secret)")
        row = QHBoxLayout()
        row.addWidget(self.paste)
        row.addWidget(button("IMPORT", self.import_connection))
        box.addLayout(row)
        form = QGridLayout()
        self.host = QLineEdit(self.settings.value("host", ""))
        self.host.setPlaceholderText("192.168.1.25")
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(int(self.settings.value("port", 8765)))
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.fingerprint = QLineEdit()
        self.fingerprint.setPlaceholderText("SHA-256 fingerprint shown on worker")
        for index, (name, field) in enumerate((("Worker IPv4", self.host), ("TCP port", self.port),
                                             ("Shared token", self.token), ("Certificate pin", self.fingerprint))):
            form.addWidget(label(name), index, 0)
            form.addWidget(field, index, 1)
        box.addLayout(form)
        row = QHBoxLayout()
        for title, action in (("CONNECT", self.connect_worker), ("DISCONNECT", self.disconnect_worker),
                              ("RECONNECT", self.connect_worker), ("SEARCH LAN", lambda: self.engine.submit("discover"))):
            row.addWidget(button(title, action))
        box.addLayout(row)
        box.addWidget(label("Discovery is an untrusted hint. Pair with the worker’s token and certificate pin.", "subtitle"))
        self.discovered = QTableWidget(0, 5)
        self.discovered.setHorizontalHeaderLabels(["Hostname", "IP / port", "GPU", "VRAM", "Discovery response"])
        self.discovered.horizontalHeader().setStretchLastSection(True)
        self.discovered.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.discovered.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.discovered.cellDoubleClicked.connect(self.select_discovered)
        box.addWidget(self.discovered)
        box.addWidget(button("CONNECT SELECTED WORKER", self.select_discovered))
        page.addWidget(self.controller_box)
        self.worker_box = QWidget()
        work = QVBoxLayout(self.worker_box)
        self.interface = QComboBox()
        for item in interfaces():
            self.interface.addItem(f"{item['name']} — {item['ip']}", item["ip"])
        self.worker_port = QSpinBox()
        self.worker_port.setRange(1024, 65535)
        self.worker_port.setValue(int(self.settings.value("worker_port", 8765)))
        self.discovery_enabled = QCheckBox("Enable optional LAN discovery (UDP 8766)")
        self.discovery_enabled.setChecked(True)
        work.addWidget(label("Bind to this private LAN interface:"))
        work.addWidget(self.interface)
        work.addWidget(self.worker_port)
        work.addWidget(self.discovery_enabled)
        row = QHBoxLayout()
        row.addWidget(button("START SERVICE", self.start_worker, True))
        row.addWidget(button("STOP SERVICE", lambda: self.engine.submit("stop_worker")))
        work.addLayout(row)
        self.worker_details = label("Start the service to generate pairing information.")
        work.addWidget(self.worker_details)
        work.addWidget(button("COPY SECURE CONNECTION INFORMATION", self.copy_invitation))
        work.addWidget(label("Transfer directly to your controller. This includes an access token; keep it private. Clipboard clears after 60 seconds if unchanged.", "subtitle"))
        work.addWidget(button("SET UP PRIVATE LAN FIREWALL RULE", self.firewall))
        page.addWidget(self.worker_box)
        page.addStretch()

    def build_benchmark(self):
        page = self.page("Verify the whole path", "CUDA allocation, computation, exact result verification, and measured TLS network transfers.")
        row = QHBoxLayout()
        row.addWidget(button("RUN FULL TEST", lambda: self.run_test("full"), True))
        row.addWidget(button("NETWORK ONLY", lambda: self.run_test("network")))
        row.addWidget(button("STOP TEST", self.stop_test))
        page.addLayout(row)
        self.tests = QTableWidget(len(STAGES), 2)
        self.tests.setHorizontalHeaderLabels(["Test", "Measured result"])
        self.tests.setColumnWidth(0, 205)
        self.tests.horizontalHeader().setStretchLastSection(True)
        self.tests.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for index, name in enumerate(STAGES):
            self.tests.setItem(index, 0, QTableWidgetItem(name))
            self.tests.setItem(index, 1, QTableWidgetItem("NOT RUN"))
        page.addWidget(self.tests)
        self.benchmark_details = label("1 / 16 / 64 / 256 MiB transfers in each direction. Results include TLS and framing overhead.")
        page.addWidget(self.benchmark_details)
        page.addWidget(label("EXCELLENT: latency <5 ms, ≥800 Mbps both ways, zero request failures. GOOD: <20 ms, ≥100 Mbps, zero failures. Otherwise LIMITED. Jitter = latency standard deviation.", "subtitle"))

    def build_stress(self):
        page = self.page("Sustained GPU test", "Real CUDA matrix multiplication. Stops at 85 °C, missing temperature telemetry, cancellation, or CUDA errors.")
        row = QHBoxLayout()
        for seconds, title in ((30, "30 SECOND GPU TEST"), (60, "1 MINUTE GPU TEST"), (300, "5 MINUTE GPU TEST")):
            row.addWidget(button(title, lambda checked=False, s=seconds: self.run_test("stress", s)))
        row.addWidget(button("STOP TEST", self.stop_test))
        page.addLayout(row)
        grid = QGridLayout()
        self.traces = []
        for index, (title, unit, color, ceiling) in enumerate((
                ("GPU utilization", "%", "#79dca4", 100), ("VRAM used", "GiB", "#76b7ff", None),
                ("Temperature", "°C", "#ffc277", 100), ("Power", "W", "#c49aff", None),
                ("GPU Link application traffic", "Mbps", "#5cd6d6", None))):
            trace = Trace(title, unit, color, ceiling)
            self.traces.append(trace)
            grid.addWidget(trace, index // 2, index % 2)
        page.addLayout(grid)
        self.stress_status = label("NOT RUN • Plots show successive live telemetry samples (up to 300 samples).")
        page.addWidget(self.stress_status)

    def build_diagnostics(self):
        page = self.page("Diagnostics", "Driver, runtime, CUDA, interface, and connectivity checks are separate conditions.")
        page.addWidget(button("REFRESH DIAGNOSTICS", lambda: self.engine.submit("diagnostics")))
        self.diagnostics = QTextEdit()
        self.diagnostics.setReadOnly(True)
        page.addWidget(self.diagnostics)

    def build_settings(self):
        page = self.page("Settings", "Mode is remembered on this Windows account. Credentials are never written to logs.")
        self.mode_choice = QComboBox()
        self.mode_choice.addItems(["MAIN / CONTROLLER", "GPU WORKER"])
        page.addWidget(self.mode_choice)
        page.addWidget(button("APPLY MODE", lambda: self.apply_mode("worker" if self.mode_choice.currentIndex() else "controller")))
        page.addWidget(label("Worker secrets and private key are protected by Windows DPAPI for your account. Controller secrets stay in memory; import them again after restart.\n\nOne worker executes one GPU job at a time on CUDA device 0. No shell commands, file paths, or executable code can be submitted.\n\nKeep both PCs on a trusted Private LAN. Never forward these ports on your router.", "subtitle"))
        page.addStretch()

    def first_run(self):
        if not self.mode:
            dialog = QDialog(self)
            dialog.setWindowTitle("Welcome to GPU Link")
            dialog.setMinimumWidth(520)
            box = QVBoxLayout(dialog)
            box.addWidget(label("WELCOME TO GPU LINK", "title"))
            box.addWidget(label("Choose the role of this PC. You can change it in Settings."))
            def choose(mode):
                self.mode = mode
                dialog.accept()
            box.addWidget(button("MAIN / CONTROLLER", lambda: choose("controller"), True))
            box.addWidget(button("GPU WORKER", lambda: choose("worker")))
            dialog.exec()
            if not self.mode:
                self.mode = "controller"
        self.apply_mode(self.mode)

    def apply_mode(self, mode):
        if self.task and self.task.isRunning():
            self.log("Stop the current test before changing modes.")
            return
        self.mode = mode
        self.settings.setValue("mode", mode)
        self.mode_choice.setCurrentIndex(1 if mode == "worker" else 0)
        self.mode_label.setText("● GPU WORKER" if mode == "worker" else "● MAIN / CONTROLLER")
        self.controller_box.setVisible(mode == "controller")
        self.worker_box.setVisible(mode == "worker")
        self.disconnect_worker()
        self.engine.submit("stop_worker")
        if mode == "worker":
            self.nav.setCurrentRow(1)
            self.start_worker()
        else:
            self.engine.submit("discover")
        self.engine.submit("diagnostics")

    def start_worker(self):
        ip = self.interface.currentData()
        if not ip:
            self.handle("error", "No private LAN IPv4 interface. Connect Ethernet/Wi-Fi and restart GPU Link.")
            return
        self.settings.setValue("worker_port", self.worker_port.value())
        self.engine.submit("start_worker", host=ip, port=self.worker_port.value(), discovery=self.discovery_enabled.isChecked())

    def import_connection(self):
        try:
            info = parse_invitation(self.paste.text().strip())
            self.host.setText(info["host"])
            self.port.setValue(info["port"])
            self.token.setText(info["token"])
            self.fingerprint.setText(info["fingerprint"])
            self.paste.clear()
            self.log("Worker connection information imported; certificate pin retained.")
        except Exception:
            self.handle("error", "Invalid connection information. Copy it again from the worker.")

    def connect_worker(self):
        if self.task and self.task.isRunning():
            self.log("Stop the current test before changing connection.")
            return
        self.credentials = {"host": self.host.text().strip(), "port": self.port.value(),
                            "token": self.token.text(), "fingerprint": self.fingerprint.text().strip()}
        if not self.credentials["token"] or not self.credentials["fingerprint"]:
            self.handle("error", "Import worker information or enter both shared token and certificate fingerprint.")
            return
        self.settings.setValue("host", self.credentials["host"])
        self.settings.setValue("port", self.credentials["port"])
        self.verified = False
        self.compute_label.setText("NOT TESTED for this connection")
        self.engine.submit("connect", **self.credentials)

    def disconnect_worker(self):
        self.stop_test()
        self.credentials = None
        self.connected = self.verified = False
        self.engine.submit("disconnect")

    def select_discovered(self, *args):
        row = self.discovered.currentRow()
        if row >= 0:
            info = self.discovered.item(row, 0).data(Qt.ItemDataRole.UserRole)
            self.host.setText(info["host"])
            self.port.setValue(info["port"])
            self.connect_worker()

    def copy_invitation(self):
        if not self.invitation:
            return
        clipboard = QApplication.clipboard()
        clipboard.setText(self.invitation)
        snapshot = self.invitation
        QTimer.singleShot(60_000, lambda: clipboard.clear() if clipboard.text() == snapshot else None)
        self.log("Connection information copied. Share only with your controller.")

    def firewall(self):
        port = self.worker_port.value()
        text = (f"Create inbound TCP {port} on Private profiles, restricted to LocalSubnet. "
                + ("Also allow UDP 8766 for discovery. " if self.discovery_enabled.isChecked() else "")
                + "Windows will request administrator approval. Windows Firewall remains enabled.")
        if QMessageBox.question(self, "Set up Windows Firewall", text) != QMessageBox.StandardButton.Yes:
            return
        commands = []
        for protocol, number in [("TCP", port)] + ([("UDP", 8766)] if self.discovery_enabled.isChecked() else []):
            name = f"GPU Link {protocol} {number} Private"
            commands.append(f"if (-not (Get-NetFirewallRule -DisplayName '{name}' -ErrorAction SilentlyContinue)) "
                + f"{{ New-NetFirewallRule -DisplayName '{name}' -Direction Inbound -Action Allow "
                + f"-Protocol {protocol} -LocalPort {number} -Profile Private -RemoteAddress LocalSubnet }}")
        if os.name == "nt":
            command = "; ".join(commands)
            code = ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe",
                '-NoProfile -NonInteractive -Command "' + command + '"', None, 0)
            self.log("Firewall setup requested; verify by connecting from the other PC." if code > 32 else "Firewall setup cancelled or failed.")

    def run_test(self, kind, seconds=30):
        if not self.credentials or not self.connected:
            self.handle("error", "Connect to a worker in Controller mode first.")
            return
        if self.task and self.task.isRunning():
            self.log("A test is already running.")
            return
        self.task = TestTask(dict(self.credentials), kind, seconds)
        self.task.event.connect(self.handle)
        self.task.start()
        self.nav.setCurrentRow(3 if kind == "stress" else 2)
        self.stress_status.setText(f"RUNNING — {seconds} seconds" if kind == "stress" else "Telemetry active")
        if kind == "full":
            self.verified = False
            self.compute_label.setText("TEST RUNNING")
        self.log(f"{kind.title()} test started")

    def stop_test(self):
        if self.task and self.task.isRunning():
            self.task.cancel.set()
            self.log("Stop requested. Current bounded network transfer / CUDA kernel must return first.")

    def log(self, text):
        logging.getLogger("gpu_link").info("%s", text)
        self.logs.append(f"[{time.strftime('%H:%M:%S')}] {text}".replace("<", "&lt;"))

    def handle(self, kind, value):
        if kind in ("local", "local_telemetry"):
            self.local.update(value)
            self.local_label.setText(gpu_text(self.local))
            if kind == "local":
                self.status.setText("LOCAL CUDA AVAILABLE — remote worker not yet verified" if value.get("torch_cuda") else "LOCAL CUDA UNAVAILABLE — inspect Diagnostics")
        elif kind in ("remote", "remote_telemetry"):
            self.remote.update(value)
            self.remote_label.setText(self.remote_system + gpu_text(self.remote))
            if kind == "remote_telemetry":
                self.plot(value)
        elif kind == "remote_system":
            self.remote_system = f"{value['hostname']} • {value['ip']}:{value['port']}\nCONNECTED\n\n"
            self.remote_label.setText(self.remote_system + gpu_text(self.remote))
            self.log(f"Authenticated worker {value['hostname']} at {value['ip']}:{value['port']}")
            self.remote_label.setToolTip(f"{value['hostname']} — {value['ip']}:{value['port']}")
        elif kind == "latency":
            self.network_label.setText(f"Heartbeat RTT  {value:.2f} ms\n" + (self.network_summary if hasattr(self, "network_summary") else "Bandwidth not measured"))
        elif kind == "status":
            if value == "WORKER STOPPED":
                self.invitation = ""
                self.worker_details.setText("Service stopped. Start it before copying connection information.")
                self.worker_status.setText("Worker service stopped" if self.mode == "worker" else "")
            self.connected = value in ("CONNECTED", "RECONNECTED")
            if not self.connected:
                self.verified = False
                if "LOST" in value:
                    self.remote_label.setText("CONNECTION LOST — last telemetry is stale")
                    self.compute_label.setText("RETEST REQUIRED after connection loss")
                elif value == "DISCONNECTED":
                    self.remote_label.setText("DISCONNECTED — no live remote telemetry")
                    self.compute_label.setText("NOT VERIFIED for an active connection")
                    self.network_label.setText("Disconnected — prior measurements are historical")
            self.status.setText(value)
            self.status.setStyleSheet("color: #8ce6af" if self.connected else "color: #ffc277")
            self.log(value)
        elif kind == "worker_started":
            self.invitation = value["invitation"]
            self.worker_details.setText(f"{socket.gethostname()}\n{value['host']}:{value['port']}\nTLS SHA-256: {value['fingerprint']}\nCopy connection information to PC 1 → Connection → Import → Connect.")
            self.status.setText("WORKER LISTENING — CUDA compute awaits verification")
            self.log(f"Worker listening at {value['host']}:{value['port']}")
        elif kind == "worker_telemetry":
            job = value.get("current_job")
            self.worker_status.setText(f"WORKER uptime {value['uptime']:.0f}s • Jobs completed {value['jobs_completed']} • Current job: {job['operation'] + ' / ' + job['status'] if job else 'Idle'} • Network: listening\nGPU COMPUTE TEST: {'PASSED' if value.get('compute_passed') else 'NOT RUN'}")
            self.plot(value)
        elif kind == "discovered":
            self.discovered.setRowCount(len(value))
            for row, info in enumerate(value):
                for col, text in enumerate((info.get("hostname"), f"{info['host']}:{info['port']}",
                    info.get("gpu"), gb(info.get("vram")), f"{info['discovery_ms']:.1f} ms")):
                    item = QTableWidgetItem(str(text))
                    item.setData(Qt.ItemDataRole.UserRole, info)
                    self.discovered.setItem(row, col, item)
            self.log(f"Discovery finished: {len(value)} worker(s). Manual connection remains available.")
        elif kind == "stage":
            name, text = value
            if name in STAGES:
                item = QTableWidgetItem(text)
                item.setForeground(QColor("#8ce6af" if text.startswith("PASS") else
                    "#ff8e8e" if text.startswith("FAIL") else "#ffc277" if text.startswith("SKIPPED") else "#e5edf8"))
                self.tests.setItem(STAGES.index(name), 1, item)
            if text != "WAITING":
                self.log(f"{name}: {text}")
        elif kind == "full_result":
            if not self.connected:
                self.log("Test finished after disconnect; readiness was not retained.")
                return
            self.verified = value["compute_verified"]
            computed = value.get("compute", {})
            self.compute_label.setText((f"{computed.get('gpu')}\nCUDA DEVICE {computed.get('device')}\nJOB COMPLETED — RESULT VERIFIED\nPeak allocation {computed.get('peak_allocated', 0) / 1024**2:.2f} MiB\nSHA-256 {computed.get('sha256')}" if self.verified else "REMOTE COMPUTE NOT VERIFIED"))
            self.status.setText("GPU LINK READY — remote CUDA verified" if value["ready"] else "GPU LINK NEEDS ATTENTION — inspect test results")
            self.status.setStyleSheet("color: #8ce6af" if value["ready"] else "color: #ffc277")
            self.handle("network_result", value["network"])
        elif kind == "network_result":
            upload, download = (value["transfers"][d][-1] for d in ("upload", "download"))
            self.network_summary = (f"Upload {upload['mbps']:.1f} Mbps / {upload['MB_s']:.1f} MB/s\n"
                f"Download {download['mbps']:.1f} Mbps / {download['MB_s']:.1f} MB/s\n"
                f"{value['quality']} • jitter {value['jitter_ms']:.2f} ms • {value['failures']}/20 failures")
            self.benchmark_details.setText(self.network_summary)
            self.network_label.setText(f"Test RTT {value['latency_ms']:.2f} ms\n" + self.network_summary)
        elif kind == "stress_result":
            self.stress_status.setText(f"COMPLETED — {value['iterations']} matrix multiplications in {value['seconds']:.1f}s on {value['gpu']}")
            self.log(self.stress_status.text())
        elif kind == "diagnostics":
            self.diagnostics.setPlainText("\n\n".join(f"{k.upper()}\n{json.dumps(v, indent=2) if isinstance(v, (list, dict)) else v}" for k, v in value.items()))
        elif kind in ("error", "test_error"):
            self.log(f"ATTENTION: {value}")
            self.status.setText(f"ATTENTION — {value}")
            if kind == "test_error":
                self.stress_status.setText(f"STOPPED — {value}")
                for row in range(self.tests.rowCount()):
                    if self.tests.item(row, 1).text() == "WAITING":
                        self.tests.setItem(row, 1, QTableWidgetItem("NOT COMPLETED — " + value))
        elif kind == "log":
            self.log(value)
        elif kind == "test_done":
            self.log("Test finished")
        local = next(iter(self.local.get("gpus", [])), {}).get("total")
        remote = next(iter(self.remote.get("gpus", [])), {}).get("total") if self.connected else None
        local_uuid = next(iter(self.local.get("gpus", [])), {}).get("uuid")
        remote_uuid = next(iter(self.remote.get("gpus", [])), {}).get("uuid")
        if local is not None and remote is not None:
            total = gb(local) + " (same physical GPU)" if local_uuid and local_uuid == remote_uuid else gb(local + remote)
        else:
            total = "Unknown until connected"
        self.resources.setText(f"AVAILABLE GPU RESOURCES   Local: {gb(local)}   Remote: {gb(remote)}   Physical total: {total}")

    def plot(self, telemetry):
        gpu = next(iter(telemetry.get("gpus", [])), {})
        now, count = time.monotonic(), telemetry.get("network_bytes", 0)
        rate = None
        if self.last_network and now > self.last_network[0]:
            rate = max(0, count - self.last_network[1]) * 8 / (now - self.last_network[0]) / 1e6
        self.last_network = (now, count)
        values = [gpu.get("utilization"), gpu.get("used") / 1024**3 if gpu.get("used") is not None else None,
                  gpu.get("temperature"), gpu.get("power"), rate]
        for trace, value in zip(self.traces, values):
            trace.add(value)

    def closeEvent(self, event):
        self.stop_test()
        self.engine.stop_event.set()
        if self.engine.isRunning() or self.task and self.task.isRunning():
            event.ignore()
            if not self.closing:
                self.closing = True
                self.status.setText("SHUTTING DOWN — stopping services and pending requests")
                self.shutdown_timer = QTimer(self)
                self.shutdown_timer.timeout.connect(self.close)
                self.shutdown_timer.start(300)
        else:
            event.accept()

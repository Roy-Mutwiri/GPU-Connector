# GPU Link

**[Download GPU Link for Windows](https://github.com/Roy-Mutwiri/GPU-Connector/releases/latest)**

Download **GPU-Link-Setup.exe** from **Releases** and run it. The ~120 MB installer contains the app and
automatically checks existing GPU Link, CUDA and Python/PyTorch environments before downloading anything.
Matching runtime files are reused after SHA-256 verification. Only missing/different files are fetched from
PyTorch's official server using bounded HTTP ranges; a complete matching local runtime needs **no download**.
Use **Check requirements** to inspect the plan without installing; use the optional existing-runtime folder
picker for a custom Python environment, CUDA directory, or another GPU Link folder.
The check targets the tested **PyTorch 2.11.0 / CUDA 12.8** requirements, rather than automatically installing
the newest versions. A CUDA Toolkit alone may not contain PyTorch or cuDNN, and newer DLLs are not assumed ABI-compatible.
Your system CUDA/PyTorch installations are never changed. The NVIDIA driver is checked separately; driver updates
are not installed automatically. **RUN FULL TEST** verifies actual GPU compatibility after installation.
Allow at least 5 GiB free for a new installation. A matching existing GPU Link installation is verified and left
unchanged; missing runtime files in that installation can be repaired. Unrelated or different app folders are not overwritten.
Then click **Launch GPU Link**. Internet is needed only to obtain missing requirements; runtime LAN use stays offline.
No separate Python installation is needed. GitHub's automatic **Source code** downloads are for developers.
For offline copying, the complete local `dist/GPU-Link` folder remains available; keep `_internal` beside the EXE.

Windows desktop controller and authenticated LAN CUDA worker. Both roles use the same executable.
The remote GPU stays in its own PC. Remote VRAM is **not unified CUDA memory**.

## Run on your two PCs

1. Copy the **entire** `dist/GPU-Link` directory to each Windows PC. Start `GPU-Link.exe`.
   Do not copy just the executable: bundled Python, Qt, PyTorch and CUDA DLLs live in `_internal`.
   A separate Python or CUDA Toolkit installation is unnecessary. A compatible NVIDIA driver is required.
2. On PC 2 (RTX 3060), choose **GPU WORKER**. The app probes the driver, NVML,
   CUDA runtime and PyTorch, generates a random token and certificate, and starts its service.
   Select the correct Ethernet/private IPv4 interface if several exist; stop/start the service after changing it.
3. Use **SET UP PRIVATE LAN FIREWALL RULE** on the worker if needed. Approve Windows UAC.
   This opens inbound TCP 8765 (or your selected port), Private profile, LocalSubnet only.
   If discovery is enabled, it also opens UDP 8766 with the same restrictions.
   It never disables the firewall or opens Public profiles. Set your trusted Ethernet network to Private.
4. On the worker, click **COPY SECURE CONNECTION INFORMATION**. Transfer it privately to PC 1.
   It contains the worker address, port, 256-bit random shared token, and TLS certificate fingerprint.
   The app clears its clipboard copy after 60 seconds if unchanged. Do not post this string in chat/logs.
5. On PC 1, choose **MAIN / CONTROLLER**, open Connection, paste the information, click IMPORT then CONNECT.
   Manual IP/port/token/fingerprint entry works even if discovery is blocked. Discovery never establishes trust.
6. Click **RUN FULL TEST**. Inspect each row, particularly allocation, remote GPU compute and result verification.
   Confirm that the reported remote device is your RTX 3060, not the local GPU.
7. Run a 30-second stress test, then longer tests if desired. Unplug/reconnect Ethernet to check recovery.

## Interpret results

* Driver available means NVML successfully queried the installed NVIDIA driver.
* CUDA runtime available means `cudaRuntimeGetVersion` and `cudaGetDeviceCount` succeeded.
* PyTorch CUDA available means the bundled CUDA build detects an accessible device.
* GPU compute passed means the worker ran `A @ B` on **cuda:0**, synchronizing CUDA before/after timing.
* Result verified means the controller independently recomputed **all 65,536 matrix entries** using
  integer arithmetic and compared them exactly, checked dimensions/seed/backend, and verified SHA-256.
  The worker returns GPU name, device ID, dimensions, kernel time, allocated/peak VRAM and result hash.
* Memory test allocates, writes and reads 64 MiB on CUDA. It is a reachability check, not a full VRAM burn-in.
* Missing/unsupported NVML readings display Unavailable, never invented zeros.
* Overall readiness requires CUDA computation and independent verification, runtime/driver checks and telemetry.
  A successful TCP connection alone is insufficient. A lost connection invalidates the previous readiness status.
* The inventory uses measured GiB. Physical total is informational and cannot be allocated as one CUDA tensor.

Network tests measure TLS application request round trips, not ICMP ping. Jitter is population standard
deviation across 20 requests. Request failures are counted; physical packet loss is not directly observable
through TCP. Upload/download transfers use reusable memory buffers at 1, 16, 64 and 256 MiB. Throughput
includes framing, TLS and transfer confirmation. Mbps uses decimal megabits and MB/s decimal megabytes.
The live network plot counts GPU Link application bytes (control messages plus benchmark data), excluding
TLS overhead and other programs' traffic. It is not a whole-interface traffic monitor.
All payload sizes appear in Logs; the dashboard summarizes the 256 MiB transfers. Loopback numbers are not LAN numbers.
EXCELLENT = mean latency <5 ms, both directions ≥800 Mbps and zero failures; GOOD = <20 ms,
both directions ≥100 Mbps and zero failures; otherwise LIMITED. Sustained test is ten seconds of heartbeats.

## Safety and security

TLS 1.2+ is mandatory. The controller checks the exact SHA-256 certificate pin **before sending the token**.
Tokens use constant-time comparison. Worker token/private key are protected with Windows DPAPI for the current
Windows account. Controller credentials remain in memory. Re-import after restart; mode/IP/port are remembered.
Data and rotating logs are under `%LOCALAPPDATA%/GPU Link` (four backups, 2 MB per file).
An encrypted temporary PEM is used to load OpenSSL; the plaintext key is never written to disk.

Worker binds to the selected private IPv4 LAN address; public IPs are rejected. Discovery listens on UDP 8766
and rejects non-LAN peers. No internet is required at runtime. Do not forward these ports on your router.
One shared token grants access to all allowed operations, including stopping jobs. Pair only trusted controllers.
To revoke a leaked token: stop GPU Link, delete `worker.secret` from its local data directory, restart and re-pair.
To replace the certificate: stop the app, remove `worker.crt` and `worker.key.dpapi`, restart and re-pair.

Protocol `GPU-LINK/1`: 4-byte big-endian JSON length, bounded to 1 MiB, UTF-8 JSON object. Initial `hello`
must agree on the exact protocol version, then `authenticate`. Allowed operations: `heartbeat`,
`get_system_info`, `get_gpu_info`, `get_telemetry`, `run_compute_test`, `run_memory_test`, `run_stress_test`,
`get_job`, `stop_job`, `network_upload_test`, `network_download_test`. No shell, eval, code execution,
pickle, user-selected filesystem paths or remote file access. Binary transfers have an authenticated ready
frame with exact byte count, 256 MiB maximum, 256 KiB chunks and a final receipt. Payload integrity is checked.
Connections: 8 maximum, 20 attempts/minute per source address, 30 operations/second per connection.
Handshake timeout: 5 seconds; JSON frame deadline: 15 seconds; transfer deadline: 120 seconds.

One CUDA job at a time. Job metadata/results retained only for the most recent job. Compute deadline: 60 seconds;
stress deadline: duration +30 seconds. A 15-second missed controller polling lease requests cancellation.
Cancellation takes effect between CUDA batches. A hung driver/kernel cannot be forcibly preempted by Python;
Windows driver recovery or restarting GPU Link may be required. Network cancellation waits for the current
bounded transfer. Stress runs 30/60/300 seconds and stops on CUDA errors, nonfinite results, missing temperature
telemetry or ≥85 °C. It changes no voltage, clocks, fan settings or driver limits.

## Develop, test and package

Python 3.11+ on Windows x64. From this directory:

```powershell
.\build.ps1
```

If your account blocks unsigned local scripts, run this script with a process-only policy override:
`powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1`. This does not change system policy.

The script creates `.venv`, installs requirements plus CUDA PyTorch 2.11.0 from the official cu128 index,
runs pytest, Ruff and compileall, then PyInstaller in one-directory mode. Output:
`dist/GPU-Link/GPU-Link.exe`. Existing prepared environments can use `build.ps1 -SkipInstall`.
CUDA libraries make the distribution large; reliability takes priority over a single-file executable.
Dependency versions resolved for the delivered build are recorded in `requirements-lock.txt`.

After the portable build, `powershell -NoProfile -ExecutionPolicy Bypass -File .\build-setup.ps1`
creates `release/GPU-Link-Setup.exe`. The installer verifies its 37 runtime library files against the tested
portable distribution. Local matches are copied privately; missing files use selective ZIP reads from the official
PyTorch wheel, and every resulting library must match its pinned SHA-256. An existing verified cached wheel is
also reused. The standalone `scripts/verify_setup_reuse.py` checks offline reuse, a no-change repeat installation,
and a real selective download from PyTorch. It requires a locally installed matching runtime for its offline case.

```powershell
.\.venv\Scripts\python.exe -m pytest -q -m "not gpu"  # CPU-safe unit/integration tests
.\.venv\Scripts\python.exe -m pytest -q               # also actual CUDA test, skipped if unavailable
.\.venv\Scripts\python.exe launcher.py
.\.venv\Scripts\python.exe launcher.py --self-test --stress --report artifacts\source-hardware.json
.\dist\GPU-Link\GPU-Link.exe --self-test --report artifacts\packaged-hardware.json
```

The self-test creates a temporary TLS worker, authenticates, runs the full suite over loopback,
verifies genuine CUDA results and writes JSON. `--stress` adds a real 30-second CUDA test.
It **does not** validate the other computer, Ethernet throughput or firewall reachability.
Tests never pretend a fixture GPU is actual hardware; GPU integration skips if CUDA is unavailable.
`scripts/gui_smoke.py` validates headless startup and writes `artifacts/dashboard.png`.
`GPU-Link.exe --gui-smoke` exercises the packaged Qt interface offscreen and writes
`artifacts/packaged-dashboard.png`; it closes automatically after telemetry loads.

## Architecture and next workloads

`app`: entry point/self-test; `ui`: Qt views, background service/test threads, plots;
`controller`: TLS client/reconnect policy/full health test; `worker`: allowlist and single job executor;
`network`: LAN interface enumeration/discovery; `protocol`: strict framing;
`gpu`: NVML/CUDA probes; `benchmarks`: CUDA jobs and independent CPU verification;
`security`: identities/pinning/DPAPI; `diagnostics`: actionable probes; `logging`: rotation.

Next add `RUN_EMBEDDING` as a typed, versioned job with a worker-owned model registry, input/output size
limits, model warmup, resource admission checks and cancellation. The controller can route whole independent
requests to either local or remote workers using measured availability. Follow with LLM/vision/TTS/STT jobs.
Model sharding/tensor parallelism requires a separate framework and sufficient interconnect bandwidth.
There is no remote VRAM pooling, local device emulation or arbitrary model/code upload.

## Practical limitations

Windows IPv4 private LANs only; device 0 used for CUDA. Multiple adapters are displayed, but the job scheduler
does not yet select among them. GPU execution/result verification assumes trusted worker software; this is
not cryptographic GPU attestation against a malicious worker that secretly computes on CPU. The app is an
interactive desktop worker, not an auto-start Windows service. Closing Worker mode stops its service.
The executable is unsigned; organizational policy/SmartScreen may require approval. Firewall reachability
is only proven by connecting from a second PC, not inferred from an existing rule. No automatic firewall
changes are made until the local operator requests setup and approves UAC.

References: [official PyTorch CUDA installation](https://pytorch.org/get-started/locally/),
[PyTorch versioned wheels](https://pytorch.org/get-started/previous-versions/),
[PyInstaller distribution specifications](https://pyinstaller.org/en/stable/spec-files.html).

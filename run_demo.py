# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Launch the Windows demo frontend and backend; configure any model proxy separately."""

import os
import subprocess
import time
from pathlib import Path

root = Path(__file__).resolve().parent

services = [
    (
        "Frontend",
        [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "npm run dev"],
        root / "frontend",
        {},
    ),
    (
        "Backend",
        ["uv", "run", "-m", "pyrit.backend.pyrit_backend", "--log-level", "info"],
        root,
        {"PYRIT_DEV_MODE": "true"},
    ),
]

running = []

try:
    for name, command, cwd, extra_env in services:
        print(f"Starting {name}...", flush=True)
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env={**os.environ, **extra_env},
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        running.append((name, process))

    print("All services launched. Press Ctrl+C to stop.", flush=True)

    while True:
        for name, process in running:
            exit_code = process.poll()
            if exit_code is not None:
                raise RuntimeError(f"{name} exited with code {exit_code}")
        time.sleep(0.5)

except KeyboardInterrupt:
    print("\nStopping demo services...", flush=True)

finally:
    for name, process in reversed(running):
        if process.poll() is None:
            print(f"Stopping {name}...", flush=True)
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
            )

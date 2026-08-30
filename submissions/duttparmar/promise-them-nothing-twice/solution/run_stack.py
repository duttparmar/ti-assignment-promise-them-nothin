#!/usr/bin/env python3
"""Start coordinator + 3 app nodes. Optional --with-harness runs the demo and exits."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COORDINATOR_PORT = 7099
APP_PORTS = (7101, 7102, 7103)


def _popen(args, extra_env):
    env = os.environ.copy()
    env.update(extra_env)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.Popen(args, cwd=str(ROOT), env=env)


def start(harness_mode: bool = True):
    procs = []
    coord_env = {
        "HARNESS_MODE": "1" if harness_mode else "0",
        "WINDOW_MS": "60000",
    }
    procs.append(
        _popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "coordinator.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(COORDINATOR_PORT),
                "--log-level",
                "warning",
            ],
            coord_env,
        )
    )
    for i, port in enumerate(APP_PORTS, start=1):
        procs.append(
            _popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--log-level",
                    "warning",
                ],
                {
                    "NODE_ID": f"node-{i}",
                    "COORDINATOR_URL": f"http://127.0.0.1:{COORDINATOR_PORT}",
                    "POLICIES_PATH": str(ROOT / "policies.yaml"),
                    "HARNESS_MODE": "1" if harness_mode else "0",
                },
            )
        )
    return procs


def wait_ready(timeout=8.0):
    import urllib.request

    deadline = time.time() + timeout
    urls = [f"http://127.0.0.1:{COORDINATOR_PORT}/health"] + [
        f"http://127.0.0.1:{p}/health" for p in APP_PORTS
    ]
    while time.time() < deadline:
        try:
            for u in urls:
                urllib.request.urlopen(u, timeout=0.3).read()
            return
        except Exception:
            time.sleep(0.1)
    raise SystemExit("stack did not become ready")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-harness", action="store_true")
    parser.add_argument("--no-harness-mode", action="store_true")
    args = parser.parse_args()
    procs = start(harness_mode=not args.no_harness_mode)
    try:
        wait_ready()
        print(
            "stack ready: coordinator :%s  nodes %s"
            % (COORDINATOR_PORT, ",".join(str(p) for p in APP_PORTS)),
            flush=True,
        )
        if args.with_harness:
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
            result = subprocess.run(
                [sys.executable, "-m", "harness.run"],
                cwd=str(ROOT),
                env=env,
            )
            raise SystemExit(result.returncode)
        print("Ctrl+C to stop")
        while True:
            time.sleep(1)
            if any(p.poll() is not None for p in procs):
                raise SystemExit("a process exited")
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
        for p in procs:
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()

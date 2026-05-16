#!/usr/bin/env python3
"""GlowUp field health checker — verifies machine readiness for its assigned role.

Runs on deployed machines (not dev boxes) to verify that all services,
dependencies, and connections required for the machine's role are
working.  Returns nonzero exit code on any failure.

Usage::

    python3 health_check.py --role server
    python3 health_check.py --role all          # detect and check all roles

No dependencies beyond Python stdlib — runs on any machine without a venv.
"""

# Copyright (c) 2026 Perry Kivolowitz. All rights reserved.
# Licensed under the MIT License. See LICENSE file in the project root.

__version__: str = "1.0"

import argparse
import json
import logging
import os
import platform
import socket
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime
from typing import Any, Optional

logger: logging.Logger = logging.getLogger("glowup.health_check")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default service check timeout (seconds).
_TIMEOUT_S: float = 5.0

# Disk usage warning threshold (percent).
_DISK_WARN_PCT: float = 85.0

# Memory warning threshold (percent).
_MEM_WARN_PCT: float = 90.0

# ANSI colors (disabled if not a terminal).
_USE_COLOR: bool = sys.stdout.isatty()

def _green(s: str) -> str:
    """Green text for PASS."""
    return f"\033[32m{s}\033[0m" if _USE_COLOR else s

def _red(s: str) -> str:
    """Red text for FAIL."""
    return f"\033[31m{s}\033[0m" if _USE_COLOR else s

def _yellow(s: str) -> str:
    """Yellow text for WARN."""
    return f"\033[33m{s}\033[0m" if _USE_COLOR else s

def _bold(s: str) -> str:
    """Bold text."""
    return f"\033[1m{s}\033[0m" if _USE_COLOR else s


# ---------------------------------------------------------------------------
# Check result tracking
# ---------------------------------------------------------------------------

class CheckResult:
    """Tracks pass/fail/warn for a single check."""

    def __init__(self) -> None:
        """Initialize empty results."""
        self.checks: list[tuple[str, str, str]] = []  # (status, name, detail)

    def passed(self, name: str, detail: str = "") -> None:
        """Record a passing check."""
        self.checks.append(("PASS", name, detail))

    def failed(self, name: str, detail: str = "") -> None:
        """Record a failing check."""
        self.checks.append(("FAIL", name, detail))

    def warn(self, name: str, detail: str = "") -> None:
        """Record a warning (non-fatal)."""
        self.checks.append(("WARN", name, detail))

    def report(self) -> int:
        """Print results and return exit code (0 = all pass)."""
        fails: int = 0
        warns: int = 0
        for status, name, detail in self.checks:
            suffix: str = f" — {detail}" if detail else ""
            if status == "PASS":
                print(f"  {_green('PASS')}  {name}{suffix}")
            elif status == "WARN":
                print(f"  {_yellow('WARN')}  {name}{suffix}")
                warns += 1
            else:
                print(f"  {_red('FAIL')}  {name}{suffix}")
                fails += 1
        total: int = len(self.checks)
        print()
        if fails:
            print(_red(f"{fails} FAILED"), end="")
        else:
            print(_green("ALL PASSED"), end="")
        if warns:
            print(f", {_yellow(f'{warns} warnings')}", end="")
        print(f" ({total} checks)")
        return 1 if fails else 0


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _systemd_active(service: str) -> bool:
    """Check if a systemd service is active."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", service],
            timeout=5,
            capture_output=True,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _systemd_enabled(service: str) -> bool:
    """Check if a systemd service is enabled at boot."""
    try:
        result = subprocess.run(
            ["systemctl", "is-enabled", "--quiet", service],
            timeout=5,
            capture_output=True,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _http_status(url: str, timeout: float = _TIMEOUT_S) -> int:
    """GET a URL and return the HTTP status code.  0 on error."""
    try:
        req = urllib.request.Request(url, method="GET")
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def _tcp_connect(host: str, port: int, timeout: float = _TIMEOUT_S) -> bool:
    """Check if a TCP port is accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.error, OSError):
        return False


def _file_exists(path: str) -> bool:
    """Check if a file exists."""
    return os.path.exists(path)


def _python_version() -> str:
    """Return Python version string."""
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def _disk_usage_pct(path: str = "/") -> float:
    """Return disk usage as a percentage."""
    st = os.statvfs(path)
    total: float = st.f_blocks * st.f_frsize
    free: float = st.f_bavail * st.f_frsize
    if total == 0:
        return 0.0
    return (1.0 - free / total) * 100.0


def _memory_usage_pct() -> Optional[float]:
    """Return memory usage as a percentage (Linux only)."""
    try:
        with open("/proc/meminfo", "r") as f:
            lines = f.readlines()
        info: dict[str, int] = {}
        for line in lines:
            parts = line.split()
            if len(parts) >= 2:
                info[parts[0].rstrip(":")] = int(parts[1])
        total: int = info.get("MemTotal", 0)
        available: int = info.get("MemAvailable", 0)
        if total == 0:
            return None
        return (1.0 - available / total) * 100.0
    except (FileNotFoundError, KeyError, ValueError):
        return None


def _process_running(pattern: str) -> bool:
    """Check if a process matching the pattern is running."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", pattern],
            timeout=5,
            capture_output=True,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ---------------------------------------------------------------------------
# Common checks (run for every role)
# ---------------------------------------------------------------------------

def check_common(r: CheckResult) -> None:
    """Checks common to all machines."""
    # Python version.
    ver: str = _python_version()
    major, minor = sys.version_info[:2]
    if major >= 3 and minor >= 10:
        r.passed("Python version", ver)
    else:
        r.failed("Python version", f"{ver} (need >= 3.10)")

    # Hostname.
    hostname: str = socket.gethostname()
    r.passed("Hostname", hostname)

    # Disk usage.
    disk_pct: float = _disk_usage_pct("/")
    if disk_pct > _DISK_WARN_PCT:
        r.warn("Disk usage", f"{disk_pct:.0f}%")
    else:
        r.passed("Disk usage", f"{disk_pct:.0f}%")

    # Memory usage (Linux only).
    mem_pct: Optional[float] = _memory_usage_pct()
    if mem_pct is not None:
        if mem_pct > _MEM_WARN_PCT:
            r.warn("Memory usage", f"{mem_pct:.0f}%")
        else:
            r.passed("Memory usage", f"{mem_pct:.0f}%")

    # Network connectivity (can reach localhost).
    if _tcp_connect("127.0.0.1", 22):
        r.passed("SSH daemon", "listening")
    else:
        r.warn("SSH daemon", "not listening on :22")


# ---------------------------------------------------------------------------
# Role: server (Pi 5 at .214)
# ---------------------------------------------------------------------------

def check_server(r: CheckResult, api_port: int = 8420) -> None:
    """Check GlowUp server health."""
    print(_bold("Server checks:"))

    # Systemd service.
    if _systemd_active("glowup-server"):
        r.passed("glowup-server service", "active")
    else:
        r.failed("glowup-server service", "not active")

    # HTTP endpoint.
    status: int = _http_status(f"http://localhost:{api_port}/api/status")
    if status in (200, 401):
        r.passed("API endpoint", f"HTTP {status}")
    else:
        r.failed("API endpoint", f"HTTP {status}")

    # MQTT broker.
    if _tcp_connect("127.0.0.1", 1883):
        r.passed("MQTT broker", "accepting connections on :1883")
    else:
        r.failed("MQTT broker", "not reachable on :1883")

    # Scheduler.
    if _systemd_active("glowup-scheduler"):
        r.passed("Scheduler service", "active")
    elif _process_running("scheduler"):
        r.passed("Scheduler", "running (in-process)")
    else:
        r.warn("Scheduler", "not detected (may be in-process)")

    # Cloudflare tunnel.
    if _systemd_active("cloudflared"):
        r.passed("Cloudflare tunnel", "active")
    elif _process_running("cloudflared"):
        r.passed("Cloudflare tunnel", "running")
    else:
        r.warn("Cloudflare tunnel", "not running")

    # Adapter proxies (check via API if auth available).
    token_path: str = os.path.expanduser("~/.glowup_token")
    if os.path.exists(token_path):
        with open(token_path, "r") as f:
            token: str = f.read().strip()
        try:
            req = urllib.request.Request(
                f"http://localhost:{api_port}/api/status",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp = urllib.request.urlopen(req, timeout=_TIMEOUT_S)
            data: dict[str, Any] = json.loads(resp.read())
            adapters: dict[str, Any] = data.get("adapters", {})
            healthy: int = 0
            for name, info in adapters.items():
                is_ok: bool = (
                    info.get("running", False)
                    or info.get("connected", False)
                    or info.get("status") == "ok"
                )
                if is_ok:
                    healthy += 1
                else:
                    r.warn(f"Adapter: {name}", "not healthy")
            if healthy:
                r.passed("Adapters", f"{healthy}/{len(adapters)} healthy")
        except Exception as exc:
            r.warn("Adapter status", f"could not query: {exc}")

    # Python venv.
    venv_path: str = os.path.expanduser("~/venv/bin/python3")
    if _file_exists(venv_path):
        r.passed("Python venv", venv_path)
    else:
        r.warn("Python venv", "not found at ~/venv")


# ---------------------------------------------------------------------------
# Role detection (auto-detect from services and processes)
# ---------------------------------------------------------------------------

def detect_roles() -> list[str]:
    """Auto-detect which roles this machine serves."""
    roles: list[str] = []

    if (_systemd_active("glowup-server")
            or _process_running("server.py")):
        roles.append("server")

    return roles


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

_ROLE_CHECKS: dict[str, Any] = {
    "server": check_server,
}


def main() -> None:
    """Parse arguments and run health checks."""
    parser = argparse.ArgumentParser(
        description="GlowUp field health checker",
    )
    parser.add_argument(
        "--role", type=str, action="append", default=None,
        choices=list(_ROLE_CHECKS.keys()) + ["all"],
        help="Machine role(s) to check. Use 'all' to auto-detect.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output results as JSON instead of text.",
    )
    args = parser.parse_args()

    roles: list[str] = args.role or ["all"]

    if "all" in roles:
        roles = detect_roles()
        if not roles:
            print("No GlowUp roles detected on this machine.")
            print("Use --role to specify: server")
            sys.exit(1)
        print(f"Detected roles: {', '.join(roles)}")

    print()
    print(_bold(f"GlowUp Health Check — {socket.gethostname()}"))
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Python {_python_version()} on {platform.system()} {platform.machine()}")
    print()

    r = CheckResult()

    # Common checks.
    print(_bold("Common checks:"))
    check_common(r)
    print()

    # Role-specific checks.
    for role in roles:
        check_fn = _ROLE_CHECKS.get(role)
        if check_fn:
            check_fn(r)
            print()

    # Summary.
    exit_code: int = r.report()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

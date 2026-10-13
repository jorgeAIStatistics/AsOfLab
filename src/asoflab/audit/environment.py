"""Describe the machine, library versions and code revision a result was produced with."""

import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

import psutil

from asoflab import CONTRACT_VERSION, __version__

_LIBRARIES = ["duckdb", "pyarrow", "numpy", "scipy", "scikit-learn", "pydantic", "hypothesis"]


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def code_revision(root: str | Path | None = None) -> dict[str, Any]:
    """Git commit of the working tree and whether it has uncommitted changes."""
    directory = Path(root) if root else Path(__file__).resolve().parents[3]

    def git(*arguments: str) -> str | None:
        try:
            done = subprocess.run(
                ["git", *arguments],
                cwd=directory,
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip()

    commit = git("rev-parse", "HEAD")
    status = git("status", "--porcelain")
    return {"commit": commit, "dirty": None if status is None else bool(status)}


def library_versions() -> dict[str, str]:
    versions = {}
    for name in _LIBRARIES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = "not installed"
    return versions


def describe_environment() -> dict[str, Any]:
    memory = psutil.virtual_memory()
    return {
        "asoflab_version": __version__,
        "contract_version": CONTRACT_VERSION,
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": _processor_name(),
        "logical_cpus": os.cpu_count(),
        "physical_cpus": psutil.cpu_count(logical=False),
        "memory_bytes": int(memory.total),
        "libraries": library_versions(),
        **{f"code_{key}": value for key, value in code_revision().items()},
    }


def _processor_name() -> str:
    if sys.platform == "darwin":
        try:
            done = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )
            return done.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
    return platform.processor() or "unknown"

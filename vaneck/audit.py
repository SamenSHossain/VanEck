"""Run provenance: every run records what code, configuration and data produced it."""
from __future__ import annotations

import hashlib
import json
import platform
import secrets
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .config import RAW_DIR, ROOT, SPEC, Spec


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)


def git_info(root: Path = ROOT) -> dict:
    def _run(*args: str) -> str | None:
        try:
            return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                                  check=True, timeout=10).stdout.strip()
        except Exception:  # noqa: BLE001 - git may be absent
            return None

    sha = _run("rev-parse", "HEAD")
    status = _run("status", "--porcelain")
    return {"sha": sha, "dirty": bool(status) if status is not None else None,
            "branch": _run("rev-parse", "--abbrev-ref", "HEAD")}


def config_hash(spec: Spec = SPEC) -> str:
    payload = json.dumps(asdict(spec), sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def data_hash(raw_dir: Path = RAW_DIR) -> str:
    """Content hash of the raw cache (order-independent, streamed)."""
    h = hashlib.sha256()
    for path in sorted(raw_dir.glob("*.csv")):
        h.update(path.name.encode())
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()[:16]


def package_versions() -> dict:
    out: dict[str, str | None] = {"python": platform.python_version()}
    for name in ("numpy", "pandas", "scipy", "yfinance", "matplotlib"):
        try:
            out[name] = __import__(name).__version__
        except Exception:  # noqa: BLE001
            out[name] = None
    return out


def run_manifest(command: str, spec: Spec = SPEC, run_id: str | None = None, extra: dict | None = None) -> dict:
    m = {
        "run_id": run_id or new_run_id(),
        "created_utc": utc_now(),
        "command": command,
        "argv": sys.argv[1:],
        "git": git_info(),
        "config_hash": config_hash(spec),
        "data_hash": data_hash(),
        "versions": package_versions(),
    }
    if extra:
        m.update(extra)
    return m


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False, default=str))
    return path

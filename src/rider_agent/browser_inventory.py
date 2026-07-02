"""Read-only Chrome process inventory for Agent browser slot heartbeat data."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rider_crawl.browser_launcher import (
    _cmdline_option_value,
    is_chrome_browser_root_cmdline,
)


@dataclass(frozen=True)
class BrowserInventorySnapshot:
    """Aggregate process counts only; no raw paths, URLs, titles, or cmdlines."""

    root_count: int = 0
    orphan_count: int = 0
    ram_used_percent: float | int | None = None


def scan_agent_chrome_inventory(profiles_root: Path) -> BrowserInventorySnapshot:
    """Scan Agent-owned Chrome browser roots under ``profiles_root``.

    Phase 1 is observational: it never terminates or mutates processes. A root is
    a Chrome process with a matching user-data-dir below ``profiles_root``, a CDP
    port, and no ``--type`` option.
    """

    psutil = _psutil_module()
    if psutil is None:
        return BrowserInventorySnapshot()

    root = Path(profiles_root)
    root_count = 0
    orphan_count = 0
    for process in psutil.process_iter(["name"]):
        try:
            name = (process.info.get("name") or "").casefold()
            if name not in {"chrome.exe", "chrome"}:
                continue
            cmdline = process.cmdline()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            continue
        if not is_chrome_browser_root_cmdline(cmdline):
            continue
        profile_dir = _cmdline_user_data_dir(cmdline)
        if profile_dir is None or not _is_relative_to(profile_dir, root):
            continue
        root_count += 1
        if _process_parent(process) is None:
            orphan_count += 1
    return BrowserInventorySnapshot(
        root_count=root_count,
        orphan_count=orphan_count,
        ram_used_percent=_system_ram_used_percent(psutil),
    )


def _psutil_module() -> Any | None:
    try:
        return importlib.import_module("psutil")
    except Exception:
        return None


def _cmdline_user_data_dir(cmdline: list[str]) -> Path | None:
    value = _cmdline_option_value(cmdline, "--user-data-dir")
    if not value:
        return None
    return Path(value)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.expanduser().resolve().relative_to(root.expanduser().resolve())
    except (OSError, ValueError):
        return False
    return True


def _process_parent(process: Any) -> Any | None:
    parent = getattr(process, "parent", None)
    if not callable(parent):
        return None
    try:
        return parent()
    except Exception:
        return None


def _system_ram_used_percent(psutil_module: Any) -> float | int | None:
    virtual_memory = getattr(psutil_module, "virtual_memory", None)
    if not callable(virtual_memory):
        return None
    try:
        value = getattr(virtual_memory(), "percent", None)
    except Exception:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return value
    return None

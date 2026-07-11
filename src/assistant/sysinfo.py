"""Local system-info using psutil — no network, instant results.

Used two ways:
  1. Fast-path answers: router matches common question patterns and calls
     individual getters directly, bypassing the LLM entirely.
  2. LLM context: get_context_string() is injected into the system prompt
     so the model can reference current stats conversationally.
"""

import time
from datetime import datetime
from typing import Optional

import psutil


def battery() -> str:
    b = psutil.sensors_battery()
    if b is None:
        return "no battery detected"
    status = "charging" if b.power_plugged else "discharging"
    return f"{b.percent:.0f}% {status}"


def cpu() -> str:
    pct = psutil.cpu_percent(interval=0.3)
    return f"{pct:.0f}%"


def ram() -> str:
    m = psutil.virtual_memory()
    free_gb = m.available / 1024 ** 3
    total_gb = m.total / 1024 ** 3
    return f"{free_gb:.1f} GB free of {total_gb:.1f} GB"


def disk(path: str = "/") -> str:
    d = psutil.disk_usage(path)
    free_gb = d.free / 1024 ** 3
    total_gb = d.total / 1024 ** 3
    return f"{free_gb:.1f} GB free of {total_gb:.1f} GB"


def uptime() -> str:
    elapsed = time.time() - psutil.boot_time()
    hours = int(elapsed // 3600)
    minutes = int((elapsed % 3600) // 60)
    if hours > 0:
        return f"{hours} hour{'s' if hours != 1 else ''} and {minutes} minutes"
    return f"{minutes} minutes"


def current_time() -> str:
    return datetime.now().strftime("%-I:%M %p")


def current_date() -> str:
    return datetime.now().strftime("%A, %B %-d")


def network_up() -> bool:
    stats = psutil.net_if_stats()
    return any(s.isup for iface, s in stats.items() if iface != "lo")


def get_context_string() -> str:
    """Compact one-line string injected into every LLM system prompt."""
    return (
        f"time={current_time()}, date={current_date()}, "
        f"battery={battery()}, cpu={cpu()}, ram={ram()}, "
        f"disk={disk()}, uptime={uptime()}, "
        f"network={'connected' if network_up() else 'disconnected'}"
    )

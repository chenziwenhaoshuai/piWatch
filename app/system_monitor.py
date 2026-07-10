from __future__ import annotations

from pathlib import Path
import threading


def parse_cpu_times(raw: str) -> tuple[int, int]:
    fields = raw.splitlines()[0].split()
    if not fields or fields[0] != "cpu":
        raise ValueError("invalid_proc_stat")
    values = [int(value) for value in fields[1:]]
    total = sum(values)
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return total, idle


def parse_meminfo(raw: str) -> dict[str, int | float]:
    values: dict[str, int] = {}
    for line in raw.splitlines():
        key, _, value = line.partition(":")
        if not value:
            continue
        values[key] = int(value.strip().split()[0]) * 1024
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", values.get("MemFree", 0))
    used = max(0, total - available)
    return {
        "total_bytes": total,
        "available_bytes": available,
        "used_bytes": used,
        "used_percent": round(used / total * 100, 1) if total else 0,
    }


class SystemMonitor:
    def __init__(self):
        self._lock = threading.Lock()
        self._previous_cpu: tuple[int, int] | None = None

    def snapshot(self) -> dict:
        with self._lock:
            current = parse_cpu_times(Path("/proc/stat").read_text(encoding="ascii"))
            previous = self._previous_cpu
            self._previous_cpu = current
        total_delta = current[0] - previous[0] if previous else current[0]
        idle_delta = current[1] - previous[1] if previous else current[1]
        cpu_percent = round(max(0.0, min(100.0, (total_delta - idle_delta) / total_delta * 100)), 1) if total_delta else 0
        return {
            "cpu_percent": cpu_percent,
            "cpu_temperature_c": self._temperature(),
            "memory": parse_meminfo(Path("/proc/meminfo").read_text(encoding="ascii")),
            "load_average": self._load_average(),
            "uptime_seconds": self._uptime(),
        }

    @staticmethod
    def _temperature() -> float | None:
        candidates: list[tuple[int, Path]] = []
        for zone in Path("/sys/class/thermal").glob("thermal_zone*"):
            try:
                zone_type = (zone / "type").read_text(encoding="ascii").strip().lower()
                priority = 0 if "cpu" in zone_type or "soc" in zone_type else 1
                candidates.append((priority, zone / "temp"))
            except OSError:
                continue
        for _, path in sorted(candidates, key=lambda item: item[0]):
            try:
                value = float(path.read_text(encoding="ascii").strip())
                return round(value / 1000 if value > 1000 else value, 1)
            except (OSError, ValueError):
                continue
        return None

    @staticmethod
    def _load_average() -> list[float]:
        try:
            return [round(float(value), 2) for value in Path("/proc/loadavg").read_text(encoding="ascii").split()[:3]]
        except (OSError, ValueError):
            return []

    @staticmethod
    def _uptime() -> int:
        try:
            return int(float(Path("/proc/uptime").read_text(encoding="ascii").split()[0]))
        except (OSError, ValueError, IndexError):
            return 0

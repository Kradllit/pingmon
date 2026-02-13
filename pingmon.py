#!/usr/bin/env python3
"""pingmon — Terminal Ping Monitor with sparkline visualization."""

import argparse
import os
import re
import subprocess
import time
from collections import deque

import psutil
from rich.box import ROUNDED
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

# ── Constants ────────────────────────────────────────────────────────────────

SPARK_CHARS = " ▁▂▃▄▅▆▇█"
DROP_CHAR = "░"
HISTORY_SIZE = 30
VITALS_SPARK_SIZE = 10
PING_TIMEOUT_MS = 2000
HIGH_LATENCY_MS = 100
PANEL_WIDTH = 58


# ── Ping ─────────────────────────────────────────────────────────────────────

def run_ping(host: str) -> float | None:
    """Ping host once. Return latency in ms or None on failure."""
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", str(PING_TIMEOUT_MS), host],
            capture_output=True,
            text=True,
            timeout=PING_TIMEOUT_MS / 1000 + 3,
        )
        match = re.search(r"time=(\d+\.?\d*)\s*ms", result.stdout)
        if match:
            return float(match.group(1))
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


# ── Sparkline ────────────────────────────────────────────────────────────────

def build_sparkline(history: deque) -> Text:
    """Build a colored sparkline Text from the latency history."""
    values = list(history)
    latencies = [v for v in values if v is not None]
    max_val = max(latencies) if latencies else 1.0
    max_val = max(max_val, 5.0)

    spark = Text()
    for v in values:
        if v is None:
            spark.append(DROP_CHAR, style="bold red")
        else:
            idx = int((v / max_val) * 8)
            idx = min(idx, 8)
            char = SPARK_CHARS[idx]
            if v > HIGH_LATENCY_MS:
                spark.append(char, style="yellow")
            else:
                spark.append(char, style="cyan")

    pad = HISTORY_SIZE - len(values)
    if pad > 0:
        spark = Text(" " * pad) + spark

    return spark


def build_vitals_sparkline(history: deque, session_max: float) -> Text:
    """Build a sparkline with auto-baseline coloring."""
    values = list(history)
    max_val = max(session_max, 1.0)

    spark = Text()
    for v in values:
        idx = int((v / max_val) * 8)
        idx = min(idx, 8)
        char = SPARK_CHARS[idx]
        ratio = v / max_val if max_val > 0 else 0
        if ratio > 0.8:
            spark.append(char, style="red")
        elif ratio > 0.6:
            spark.append(char, style="yellow")
        else:
            spark.append(char, style="cyan")

    pad = VITALS_SPARK_SIZE - len(values)
    if pad > 0:
        spark = Text(" " * pad) + spark

    return spark


# ── Vitals ───────────────────────────────────────────────────────────────────

def format_bytes(n: float) -> str:
    """Format bytes to compact human-readable string."""
    if n >= 1 << 30:
        return f"{n / (1 << 30):.1f}G"
    elif n >= 1 << 20:
        return f"{n / (1 << 20):.0f}M"
    elif n >= 1 << 10:
        return f"{n / (1 << 10):.0f}K"
    return f"{n:.0f}B"


def cpu_color(pct: float) -> str:
    if pct > 90:
        return "bold red"
    elif pct > 60:
        return "bold yellow"
    return "bold green"


def mem_color(pct: float) -> str:
    if pct > 90:
        return "bold red"
    elif pct > 70:
        return "bold yellow"
    return "bold green"


def build_vitals_line(
    cpu_pct: float,
    cpu_hist: deque,
    cpu_max: float,
    mem: object,
    io_read_rate: float,
    io_write_rate: float,
) -> Text:
    """Build the system vitals line."""
    line = Text()

    # CPU — "CPU  12%" fixed 8 chars + sparkline 10 chars
    line.append(" CPU ", style="dim")
    line.append(f"{cpu_pct:3.0f}%", style=cpu_color(cpu_pct))
    line.append(build_vitals_sparkline(cpu_hist, cpu_max))

    line.append(" │ ", style="dim")

    # Memory — "MEM 82%"
    line.append("MEM ", style="dim")
    line.append(f"{mem.percent:2.0f}%", style=mem_color(mem.percent))

    line.append(" │ ", style="dim")

    # IO — "IO ▲200K ▼17K/s"
    line.append("IO ", style="dim")
    line.append(f"▲{format_bytes(io_read_rate)}", style="cyan")
    line.append(f" ▼{format_bytes(io_write_rate)}", style="magenta")
    line.append("/s", style="dim")

    return line


# ── Panel ────────────────────────────────────────────────────────────────────

def build_panel(host: str, history: deque, vitals_line: Text | None = None) -> Panel:
    """Build the status panel."""
    values = list(history)
    drops = sum(1 for v in values if v is None)
    total = len(values)
    alert = drops > 0
    border = "red" if alert else "green"

    # Ping line
    content = Text()
    if alert:
        content.append(" ⚠ ", style="bold yellow")
    else:
        content.append(" ● ", style="bold green")

    latest = values[-1] if values else None
    if latest is not None:
        latency_str = f"{latest:.1f}ms"
        style = "bold yellow" if latest > HIGH_LATENCY_MS else "bold green"
        content.append(f"{latency_str:<9}", style=style)
    else:
        content.append("timeout  ", style="bold red")

    content.append(build_sparkline(history))

    content.append("   ")
    if alert:
        if drops == 1:
            content.append(f"{drops} drop! ", style="bold red")
        else:
            content.append(f"{drops} drops!", style="bold red")
    else:
        if total > 0:
            content.append("0% loss", style="dim green")
        else:
            content.append("waiting", style="dim")

    # Add vitals line with horizontal divider
    if vitals_line is not None:
        # Panel inner width = total - 2 (borders) - 2 (padding)
        divider_width = PANEL_WIDTH - 4
        content.append("\n")
        content.append("─" * divider_width, style="dim")
        content.append("\n")
        content.append(vitals_line)

    return Panel(
        content,
        box=ROUNDED,
        title=host,
        title_align="left",
        border_style=border,
        width=PANEL_WIDTH,
    )


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Terminal ping monitor with sparkline")
    parser.add_argument("host", nargs="?", default="8.8.8.8", help="Host to ping (default: 8.8.8.8)")
    parser.add_argument("-i", "--interval", type=float, default=1.0, help="Ping interval in seconds (default: 1)")
    args = parser.parse_args()

    history: deque = deque(maxlen=HISTORY_SIZE)
    cpu_hist: deque = deque(maxlen=VITALS_SPARK_SIZE)
    io_hist: deque = deque(maxlen=VITALS_SPARK_SIZE)
    cpu_max = 1.0
    io_max = 1.0

    os.system("clear")

    # Prime psutil readings
    psutil.cpu_percent()
    prev_io = psutil.disk_io_counters()
    prev_time = time.monotonic()

    try:
        with Live(build_panel(args.host, history), refresh_per_second=2, transient=True) as live:
            while True:
                t0 = time.monotonic()

                # Ping
                latency = run_ping(args.host)
                history.append(latency)

                # CPU
                cpu_pct = psutil.cpu_percent()
                cpu_hist.append(cpu_pct)
                cpu_max = max(cpu_max, cpu_pct, 10.0)

                # IO
                cur_io = psutil.disk_io_counters()
                dt = time.monotonic() - prev_time
                if dt > 0 and cur_io and prev_io:
                    io_read_rate = (cur_io.read_bytes - prev_io.read_bytes) / dt
                    io_write_rate = (cur_io.write_bytes - prev_io.write_bytes) / dt
                else:
                    io_read_rate = 0.0
                    io_write_rate = 0.0
                io_combined = io_read_rate + io_write_rate
                io_hist.append(io_combined)
                io_max = max(io_max, io_combined, 1024.0)
                prev_io = cur_io
                prev_time = time.monotonic()

                # Memory
                mem = psutil.virtual_memory()

                vitals = build_vitals_line(
                    cpu_pct, cpu_hist, cpu_max,
                    mem,
                    io_read_rate, io_write_rate,
                )
                live.update(build_panel(args.host, history, vitals))

                elapsed = time.monotonic() - t0
                sleep_time = max(0, args.interval - elapsed)
                time.sleep(sleep_time)
    except KeyboardInterrupt:
        print()
        from rich.console import Console
        mem = psutil.virtual_memory()
        cpu_pct = cpu_hist[-1] if cpu_hist else 0
        vitals = build_vitals_line(
            cpu_pct, cpu_hist, cpu_max,
            mem, 0, 0,
        )
        Console().print(build_panel(args.host, history, vitals))


if __name__ == "__main__":
    main()

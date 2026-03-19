"""
Live Terminal Dashboard — Rich-based pipeline monitor
======================================================
Reads dashboard_data/progress.json + dashboard_data/benchmark.csv and displays a live dashboard.

Usage:
  python3.11 dashboard.py              # Live monitoring
  python3.11 dashboard.py --test       # One-shot test render
  python3.11 dashboard.py --interval 5 # Update every 5 seconds (default: 3)

Does NOT modify batch_processor — it's a read-only monitor.
"""

import json
import csv
import time
import shutil
import argparse
import re
import sys
from pathlib import Path
from collections import Counter

PIPELINE_ROOT = Path(__file__).resolve().parent.parent
if str(PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINE_ROOT))

from dashboard_paths import BENCHMARK_CSV, PROGRESS_FILE, ensure_dashboard_data_dir


def _status_key(value):
    value = str(value or "").upper()
    if "SUCCESS" in value:
        return "success"
    if "FAIL" in value:
        return "failed"
    if "SKIP" in value:
        return "skipped"
    return value.lower()


def _parse_secs(value):
    text = str(value or "").strip()
    if not text:
        return 0.0
    total = 0.0
    minute_match = re.search(r"(\d+\.?\d*)m", text)
    second_match = re.search(r"(\d+\.?\d*)s", text)
    if minute_match:
        total += float(minute_match.group(1)) * 60
    if second_match:
        total += float(second_match.group(1))
    if not minute_match and not second_match:
        try:
            total = float(text)
        except Exception:
            return 0.0
    return total


def _load_progress():
    """Load progress.json safely."""
    ensure_dashboard_data_dir()
    try:
        if PROGRESS_FILE.exists():
            with open(PROGRESS_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _load_recent_benchmarks(n=10):
    """Load last N rows from benchmark.csv."""
    ensure_dashboard_data_dir()
    try:
        if not BENCHMARK_CSV.exists():
            return []
        with open(BENCHMARK_CSV, 'r', encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
        return rows[-n:]
    except Exception:
        return []


def _load_all_benchmarks():
    """Load all rows from benchmark.csv for stats."""
    ensure_dashboard_data_dir()
    try:
        if not BENCHMARK_CSV.exists():
            return []
        with open(BENCHMARK_CSV, 'r', encoding='utf-8') as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def _get_disk_free():
    """Get free disk space in GB."""
    try:
        usage = shutil.disk_usage(".")
        return usage.free / (1024**3)
    except Exception:
        return -1


def _render_bar(value, total, width=30, fill="█", empty="░"):
    """Render a text progress bar."""
    if total <= 0:
        return empty * width
    pct = min(value / total, 1.0)
    filled = int(width * pct)
    return fill * filled + empty * (width - filled)


def render_dashboard():
    """Render one frame of the dashboard to terminal."""
    progress = _load_progress()
    recent = _load_recent_benchmarks(8)
    all_rows = _load_all_benchmarks()
    disk_free = _get_disk_free()

    # Terminal width
    try:
        term_width = shutil.get_terminal_size().columns
    except Exception:
        term_width = 80

    width = min(term_width, 80)
    line = "═" * width

    # Clear screen
    print("\033[2J\033[H", end="")

    # Header
    print(f"╔{line}╗")
    print(f"║{'🎵 LYRICFLOW PIPELINE DASHBOARD':^{width}}║")
    print(f"╠{line}╣")

    # Progress section
    total = progress.get("total", 0)
    completed = progress.get("completed", 0)
    failed = progress.get("failed", 0)
    in_prog = progress.get("in_progress", 0)
    remaining = progress.get("remaining", 0)
    elapsed = progress.get("elapsed_minutes", 0)
    eta = progress.get("eta_minutes", 0)
    updated = progress.get("updated_at", "N/A")

    if total > 0:
        bar = _render_bar(completed + failed, total, width=40)
        pct = ((completed + failed) / total) * 100
        print(f"║  Progress: {bar} {pct:5.1f}%")
        print(f"║  ✅ {completed:4d} Success  ❌ {failed:4d} Failed  🔄 {in_prog:2d} Active  📋 {remaining:4d} Remaining")
        print(f"║  ⏱️  Elapsed: {elapsed:.0f}m  |  ETA: {eta:.0f}m  |  Updated: {updated}")
    else:
        print(f"║  ⏳ Waiting for pipeline to start...")
        print(f"║  (Ensure batch_processor is running)")

    print(f"╠{line}╣")

    # Stats section
    statuses = Counter(_status_key(r.get("Status", "")) for r in all_rows if r.get("Status") not in (None, ""))
    success_total = statuses.get("success", 0)
    failed_total = statuses.get("failed", 0)

    # Compute throughput
    total_times = [_parse_secs(r.get("Total Time", 0)) for r in all_rows if _status_key(r.get("Status")) == "success"]
    if total_times:
        avg_per_song = sum(total_times) / len(total_times)
        throughput = 3600 / avg_per_song if avg_per_song > 0 else 0
    else:
        avg_per_song = throughput = 0

    print(f"║  📊 STATS")
    print(f"║  Songs/hr: {throughput:5.1f}  |  Avg time: {avg_per_song:.0f}s  |  Total: {success_total + failed_total}")
    print(f"║  💾 Disk free: {disk_free:.1f} GB")

    print(f"╠{line}╣")

    # Recent activity
    print(f"║  📋 RECENT ACTIVITY")
    if recent:
        for r in recent:
            status_icon = "✅" if _status_key(r.get("Status")) == "success" else "❌"
            name = (r.get("Song Name", "?"))[:30]
            t = r.get("Total Time", "?")
            step = r.get("Failed At", "") or ""
            print(f"║    {status_icon} {name:<32} {t:>8}  {step}")
    else:
        print(f"║    No data yet...")

    print(f"╠{line}╣")

    # Failure breakdown  
    failure_reasons = Counter(r.get("Failed At", "Unknown") for r in all_rows if _status_key(r.get("Status")) == "failed")
    if failure_reasons:
        print(f"║  ❌ FAILURE BREAKDOWN")
        for reason, count in failure_reasons.most_common(5):
            print(f"║    {reason}: {count}")
    else:
        print(f"║  ✅ No failures recorded")

    print(f"╚{line}╝")
    print(f"  Press Ctrl+C to exit  |  Refreshing every few seconds...")


def main():
    ensure_dashboard_data_dir()
    parser = argparse.ArgumentParser(description="LyricFlow Pipeline Dashboard")
    parser.add_argument("--interval", type=int, default=3, help="Refresh interval in seconds")
    parser.add_argument("--test", action="store_true", help="Render once and exit")
    args = parser.parse_args()

    if args.test:
        render_dashboard()
        return

    try:
        while True:
            render_dashboard()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n\n👋 Dashboard closed.")


if __name__ == "__main__":
    main()

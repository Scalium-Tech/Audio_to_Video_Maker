"""
Batch Analytics Report Generator
==================================
Generates an HTML report from dashboard_data/benchmark.csv after a batch completes.

Usage:
  python3.11 report_generator.py                  # Auto-reads dashboard_data/benchmark.csv
  python3.11 report_generator.py --test            # Generate sample report
  python3.11 report_generator.py path/to/file.csv  # Custom CSV path
"""

import csv
import time
import re
import sys
from pathlib import Path
from collections import Counter

PIPELINE_ROOT = Path(__file__).resolve().parent.parent
if str(PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINE_ROOT))

from dashboard_paths import BATCH_REPORT_HTML, BENCHMARK_CSV, ensure_dashboard_data_dir


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


def generate_report(csv_path=BENCHMARK_CSV, output_path=None):
    """
    Generate an HTML report from benchmark CSV data.
    Returns the output path.
    """
    ensure_dashboard_data_dir()
    csv_path = Path(csv_path)
    if not csv_path.exists():
        print(f"  ⚠️  Benchmark CSV not found: {csv_path}")
        return None

    if output_path is None:
        output_path = BATCH_REPORT_HTML
    else:
        output_path = Path(output_path)

    # Parse CSV
    rows = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if not rows:
        print("  ⚠️  No data in benchmark CSV")
        return None

    # Compute stats
    statuses = Counter(_status_key(r.get("Status", "unknown")) for r in rows)
    success_count = statuses.get("success", 0)
    failed_count = statuses.get("failed", 0)
    total = len(rows)

    # Timing stats (skip summary rows and empty values)
    total_times = []
    extract_times = []
    punct_times = []
    align_times = []
    render_times = []

    for r in rows:
        try:
            t = _parse_secs(r.get("Total Time", 0))
            if t > 0:
                total_times.append(t)
            e = _parse_secs(r.get("Extract Lyrics", 0))
            if e >= 0: extract_times.append(e)
            p = _parse_secs(r.get("Punctuation", 0))
            if p >= 0: punct_times.append(p)
            a = _parse_secs(r.get("NeMo Alignment", 0))
            if a >= 0: align_times.append(a)
            rv = _parse_secs(r.get("Render Video", 0))
            if rv >= 0: render_times.append(rv)
        except (ValueError, TypeError):
            pass

    avg_total = sum(total_times) / max(len(total_times), 1)
    avg_align = sum(align_times) / max(len(align_times), 1)
    avg_render = sum(render_times) / max(len(render_times), 1)

    # Failure breakdown
    failure_reasons = Counter()
    for r in rows:
        if _status_key(r.get("Status")) == "failed":
            reason = r.get("Failed At", "Unknown")
            failure_reasons[reason] += 1

    # Generate SVG bar chart for step timings
    steps = [
        ("Extract", sum(extract_times) / max(len(extract_times), 1)),
        ("Punctuation", sum(punct_times) / max(len(punct_times), 1)),
        ("Alignment", avg_align),
        ("Render", avg_render),
    ]
    max_val = max(s[1] for s in steps) if steps else 1
    bar_height = 30
    chart_width = 500
    chart_svg = f'<svg width="{chart_width + 150}" height="{len(steps) * (bar_height + 10) + 20}" xmlns="http://www.w3.org/2000/svg">'
    colors = ["#4CAF50", "#2196F3", "#FF9800", "#E91E63"]
    for i, (name, val) in enumerate(steps):
        y = i * (bar_height + 10) + 10
        w = (val / max(max_val, 0.1)) * chart_width
        chart_svg += f'<rect x="100" y="{y}" width="{w:.0f}" height="{bar_height}" fill="{colors[i]}" rx="4"/>'
        chart_svg += f'<text x="95" y="{y + 20}" text-anchor="end" fill="#333" font-size="14">{name}</text>'
        chart_svg += f'<text x="{105 + w}" y="{y + 20}" fill="#333" font-size="12">{val:.1f}s</text>'
    chart_svg += '</svg>'

    # Throughput (songs/hour)
    if total_times:
        batch_duration = sum(total_times)
        throughput = (len(total_times) / batch_duration) * 3600
    else:
        throughput = 0

    # Build HTML
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Batch Report — LyricFlow Pipeline</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; background: #f5f5f5; color: #333; }}
  h1 {{ color: #1a1a1a; border-bottom: 3px solid #E91E63; padding-bottom: 10px; }}
  h2 {{ color: #444; margin-top: 30px; }}
  .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 15px; margin: 20px 0; }}
  .stat {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); text-align: center; }}
  .stat-value {{ font-size: 2em; font-weight: bold; }}
  .stat-label {{ color: #888; margin-top: 5px; }}
  .success {{ color: #4CAF50; }}
  .failed {{ color: #E91E63; }}
  .info {{ color: #2196F3; }}
  table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin: 15px 0; }}
  th, td {{ padding: 10px 15px; text-align: left; border-bottom: 1px solid #eee; }}
  th {{ background: #333; color: white; font-weight: 500; }}
  tr:last-child td {{ border-bottom: none; }}
  .chart {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin: 15px 0; }}
  footer {{ color: #999; text-align: center; margin-top: 40px; font-size: 0.9em; }}
</style>
</head>
<body>
<h1>📊 Batch Report</h1>
<p>Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}</p>

<div class="stats">
  <div class="stat"><div class="stat-value">{total}</div><div class="stat-label">Total Songs</div></div>
  <div class="stat"><div class="stat-value success">{success_count}</div><div class="stat-label">Successful</div></div>
  <div class="stat"><div class="stat-value failed">{failed_count}</div><div class="stat-label">Failed</div></div>
  <div class="stat"><div class="stat-value info">{throughput:.1f}</div><div class="stat-label">Songs/Hour</div></div>
</div>

<h2>⏱️ Average Step Timings</h2>
<div class="chart">
  {chart_svg}
</div>

<div class="stats">
  <div class="stat"><div class="stat-value info">{avg_total:.0f}s</div><div class="stat-label">Avg Total Time</div></div>
  <div class="stat"><div class="stat-value info">{avg_align:.0f}s</div><div class="stat-label">Avg Alignment</div></div>
  <div class="stat"><div class="stat-value info">{avg_render:.0f}s</div><div class="stat-label">Avg Render</div></div>
</div>
"""

    if failure_reasons:
        html += "<h2>❌ Failure Breakdown</h2>\n<table>\n<tr><th>Failed At</th><th>Count</th></tr>\n"
        for reason, count in failure_reasons.most_common():
            html += f"<tr><td>{reason}</td><td>{count}</td></tr>\n"
        html += "</table>\n"

    # Recent failures table
    failed_rows = [r for r in rows if _status_key(r.get("Status")) == "failed"]
    if failed_rows:
        html += "<h2>🔍 Recent Failures</h2>\n<table>\n<tr><th>Song</th><th>Failed At</th><th>Error</th><th>Time</th></tr>\n"
        for r in failed_rows[-20:]:  # Last 20
            error = (r.get("Error", "") or "")[:80]
            html += f"<tr><td>{r.get('Song Name', '?')}</td><td>{r.get('Failed At', '?')}</td><td>{error}</td><td>{r.get('Timestamp', '')}</td></tr>\n"
        html += "</table>\n"

    html += """
<footer>LyricFlow Pipeline — Batch Analytics Report</footer>
</body>
</html>"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"  📊 Report generated: {output_path}")
    return str(output_path)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate batch analytics report")
    parser.add_argument("csv", nargs="?", default=str(BENCHMARK_CSV))
    parser.add_argument("--test", action="store_true", help="Generate test report")
    args = parser.parse_args()

    if args.test:
        # Create sample CSV
        test_csv = Path("/tmp/test_benchmark.csv")
        with open(test_csv, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(["Song Name", "Status", "Extract Lyrics", "Punctuation", "NeMo Alignment", "Render Video", "Total Time", "Failed At", "Error", "Timestamp"])
            w.writerow(["song1", "success", "0.1", "3.2", "28.5", "180.0", "211.8", "", "", "2026-03-13"])
            w.writerow(["song2", "success", "0.1", "2.8", "25.1", "165.0", "193.0", "", "", "2026-03-13"])
            w.writerow(["song3", "failed", "0.1", "3.0", "0", "0", "3.1", "NeMo Alignment", "timeout", "2026-03-13"])
        generate_report(str(test_csv), "/tmp/test_report.html")
        print("Test report: /tmp/test_report.html")
    else:
        generate_report(args.csv)

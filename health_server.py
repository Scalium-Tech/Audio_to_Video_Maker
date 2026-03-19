"""
Health Check HTTP Server
========================
Lightweight HTTP endpoint for remote pipeline monitoring.
Reads progress.json and returns status as JSON.

Start standalone:   python3.11 health_server.py [--port 8585]
Auto-started by batch_processor when processing begins.

Endpoints:
  GET /status  → JSON with pipeline progress
  GET /        → Simple HTML dashboard
"""

import json
import threading
import sys
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

PIPELINE_ROOT = Path(__file__).resolve().parent.parent
if str(PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINE_ROOT))

from dashboard_paths import PROGRESS_FILE, ensure_dashboard_data_dir

DEFAULT_PORT = 8585


class _HealthHandler(BaseHTTPRequestHandler):
    """HTTP handler for health check requests."""
    
    def log_message(self, format, *args):
        """Suppress default access logging."""
        pass
    
    def do_GET(self):
        if self.path == "/status" or self.path == "/status/":
            self._serve_status()
        elif self.path == "/" or self.path == "/health":
            self._serve_html()
        else:
            self.send_error(404)
    
    def _read_progress(self):
        """Read progress.json safely."""
        ensure_dashboard_data_dir()
        try:
            if PROGRESS_FILE.exists():
                return json.loads(PROGRESS_FILE.read_text())
        except Exception:
            pass
        return {"status": "no data", "message": "Pipeline not yet started or progress.json missing."}
    
    def _serve_status(self):
        """Return progress as JSON."""
        data = self._read_progress()
        body = json.dumps(data, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    
    def _serve_html(self):
        """Return a simple HTML status page."""
        data = self._read_progress()
        total = data.get("total", 0)
        success = data.get("completed", data.get("success", 0))
        failed = data.get("failed", 0)
        active = data.get("in_progress", data.get("active", 0))
        remaining = data.get("remaining", total - success - failed)
        pct = (success / total * 100) if total > 0 else 0
        
        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>LyricFlow Pipeline Status</title>
    <meta http-equiv="refresh" content="10">
    <style>
        body {{ font-family: -apple-system, sans-serif; background: #1a1a2e; color: #eee;
               display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }}
        .card {{ background: #16213e; border-radius: 16px; padding: 40px; max-width: 500px;
                box-shadow: 0 20px 60px rgba(0,0,0,0.5); }}
        h1 {{ color: #e94560; margin: 0 0 20px 0; font-size: 24px; }}
        .bar {{ background: #0f3460; border-radius: 8px; height: 24px; overflow: hidden; margin: 16px 0; }}
        .fill {{ background: linear-gradient(90deg, #e94560, #0f3460); height: 100%;
                border-radius: 8px; transition: width 1s; }}
        .stats {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 20px; }}
        .stat {{ background: #0f3460; border-radius: 8px; padding: 12px; text-align: center; }}
        .stat .num {{ font-size: 28px; font-weight: bold; }}
        .stat .label {{ font-size: 12px; color: #999; margin-top: 4px; }}
        .ok {{ color: #4ecca3; }} .err {{ color: #e94560; }} .act {{ color: #f0c929; }}
        .footer {{ text-align: center; margin-top: 16px; font-size: 11px; color: #555; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>🎵 LyricFlow Pipeline</h1>
        <div class="bar"><div class="fill" style="width:{pct:.0f}%"></div></div>
        <div style="text-align:center;font-size:20px">{pct:.1f}% Complete</div>
        <div class="stats">
            <div class="stat"><div class="num ok">{success}</div><div class="label">Success</div></div>
            <div class="stat"><div class="num err">{failed}</div><div class="label">Failed</div></div>
            <div class="stat"><div class="num act">{active}</div><div class="label">Active</div></div>
            <div class="stat"><div class="num">{remaining}</div><div class="label">Remaining</div></div>
        </div>
        <div class="footer">Auto-refreshes every 10s · <a href="/status" style="color:#e94560">JSON API</a></div>
    </div>
</body>
</html>"""
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class HealthServer:
    """Manages the health check HTTP server in a background thread."""
    
    def __init__(self, port=DEFAULT_PORT):
        self.port = port
        self._server = None
        self._thread = None
    
    def start(self):
        """Start the health server in a background thread."""
        try:
            self._server = HTTPServer(("0.0.0.0", self.port), _HealthHandler)
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
            print(f"  🏥 Health check: http://localhost:{self.port}/status", flush=True)
            return True
        except OSError as e:
            print(f"  ⚠️  Health server failed to start on port {self.port}: {e}", flush=True)
            return False
    
    def stop(self):
        """Stop the health server."""
        if self._server:
            self._server.shutdown()
            self._server = None


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="LyricFlow Health Check Server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port (default: {DEFAULT_PORT})")
    args = parser.parse_args()
    ensure_dashboard_data_dir()
    
    print(f"Starting health server on port {args.port}...")
    print(f"  Status: http://localhost:{args.port}/status")
    print(f"  Dashboard: http://localhost:{args.port}/")
    print(f"  Press Ctrl+C to stop.\n")
    
    server = HTTPServer(("0.0.0.0", args.port), _HealthHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")

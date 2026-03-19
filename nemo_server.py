"""
NeMo Model Server — Shared Model for Parallel Workers
======================================================
Loads the NeMo Hindi CTC model ONCE in a dedicated process and serves
log-probability requests to all workers via multiprocessing queues.

This saves ~500 MB RAM per worker since the model is only loaded once
instead of once per parallel worker.

Features:
  - Health check: ping the server to verify it's responsive
  - Auto-restart: if the server hangs, it is automatically restarted (up to 3 times)
  - Increased timeouts: 300s startup, 300s per request (up from 120s)

Usage:
    # Start server (usually done by batch_processor or start script):
    server = NemoModelServer()
    server.start()

    # From worker processes (via NemoModelClient):
    client = NemoModelClient()
    log_probs, audio_duration = client.get_log_probs("/path/to/audio.wav")

    # Shutdown:
    server.stop()
"""

import os
import sys
import signal
import tempfile
import subprocess
import numpy as np
from pathlib import Path
from multiprocessing import Process, Queue, Event
import multiprocessing
import time


# Sentinel values
_SHUTDOWN = "__SHUTDOWN__"
_HEALTH_CHECK = "__HEALTH_CHECK__"
_SERVER_PID_FILE = Path(__file__).parent / "output_song" / ".nemo_server.pid"
_SERVER_READY_FILE = Path(__file__).parent / "output_song" / ".nemo_server.ready"

# Timeouts (seconds)
SERVER_STARTUP_TIMEOUT = 300    # Time to wait for model to load (was 120s)
CLIENT_REQUEST_TIMEOUT = 300    # Time to wait for a single inference request (was 120s)
HEALTH_CHECK_TIMEOUT = 15       # Time to wait for health check response
MAX_RESTARTS = 3                # Max auto-restart attempts before giving up


def _convert_to_wav_simple(audio_path, output_wav):
    """Convert audio to 16kHz mono WAV."""
    cmd = [
        "ffmpeg", "-y", "-i", str(audio_path),
        "-ar", "16000", "-ac", "1", "-f", "wav",
        str(output_wav)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg conversion failed: {result.stderr[:200]}")
    return str(output_wav)


def _server_loop(request_queue, response_queues_registry, ready_event, shutdown_event):
    """
    Main server loop. Runs in a dedicated process.
    Loads the model once, then serves log-prob requests.
    Also responds to health check pings.
    """
    import torch
    from nemo.collections.asr.models import ASRModel
    import soundfile as sf

    model_name = "stt_hi_conformer_ctc_medium"
    print(f"  [NeMo Server] Loading model: {model_name}...", flush=True)
    model = ASRModel.from_pretrained(model_name, map_location="cpu")
    model.eval()

    # Extract vocabulary info
    vocab = model.decoder.vocabulary
    vocab_info = {
        "vocab": list(vocab),
        "blank_id": len(vocab),
    }

    print(f"  [NeMo Server] Model ready. Vocabulary: {len(vocab)} + blank", flush=True)
    ready_event.set()

    while not shutdown_event.is_set():
        try:
            # Wait for a request (timeout allows checking shutdown)
            try:
                request = request_queue.get(timeout=1.0)
            except Exception:
                continue

            if request == _SHUTDOWN:
                print("  [NeMo Server] Shutdown signal received.", flush=True)
                break

            # Health check: respond immediately with "ok"
            if isinstance(request, tuple) and len(request) == 2 and request[1] == _HEALTH_CHECK:
                worker_id = request[0]
                if worker_id in response_queues_registry:
                    response_queues_registry[worker_id].put({"status": "ok", "health": True})
                continue

            worker_id, wav_path = request

            try:
                # Read audio
                audio_data, sr = sf.read(wav_path)
                audio_duration = len(audio_data) / sr

                # Get log-probs
                audio_tensor = torch.tensor(audio_data, dtype=torch.float32).unsqueeze(0)
                audio_len = torch.tensor([len(audio_data)], dtype=torch.int64)

                with torch.no_grad():
                    outputs = model.forward(
                        input_signal=audio_tensor, input_signal_length=audio_len
                    )
                    if isinstance(outputs, tuple):
                        log_probs = outputs[0]
                    else:
                        log_probs = outputs

                log_probs_np = log_probs[0].cpu().numpy()

                # Send response
                if worker_id in response_queues_registry:
                    response_queues_registry[worker_id].put({
                        "status": "ok",
                        "log_probs": log_probs_np,
                        "audio_duration": audio_duration,
                        "vocab_info": vocab_info,
                    })
                    
            except Exception as e:
                if worker_id in response_queues_registry:
                    response_queues_registry[worker_id].put({
                        "status": "error",
                        "error": str(e),
                    })

        except Exception as e:
            print(f"  [NeMo Server] Error in loop: {e}", flush=True)
            continue

    print("  [NeMo Server] Stopped.", flush=True)


class NemoModelServer:
    """
    Manages the NeMo model server process.
    Start this once before batch processing.
    
    Features:
      - is_healthy(): ping the server to verify it's responsive
      - ensure_alive(): auto-restart if unhealthy (up to MAX_RESTARTS times)
    """

    def __init__(self):
        self.request_queue = multiprocessing.Manager().Queue()
        self._manager = multiprocessing.Manager()
        self.response_queues = self._manager.dict()
        self.ready_event = multiprocessing.Event()
        self.shutdown_event = multiprocessing.Event()
        self.process = None
        self._restart_count = 0
        self._health_queue = self._manager.Queue()
        self.response_queues["__health__"] = self._health_queue

    def start(self):
        """Start the model server process."""
        print("  [NeMo Server] Starting...", flush=True)

        # Reset events for fresh start
        self.ready_event.clear()
        self.shutdown_event.clear()

        self.process = Process(
            target=_server_loop,
            args=(self.request_queue, self.response_queues,
                  self.ready_event, self.shutdown_event),
            daemon=True
        )
        self.process.start()

        # Write PID file
        _SERVER_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SERVER_PID_FILE.write_text(str(self.process.pid))

        # Wait for model to load (increased to 300s)
        print(f"  [NeMo Server] Waiting for model to load (timeout: {SERVER_STARTUP_TIMEOUT}s)...", flush=True)
        self.ready_event.wait(timeout=SERVER_STARTUP_TIMEOUT)

        if self.ready_event.is_set():
            _SERVER_READY_FILE.touch()
            print("  [NeMo Server] Ready!", flush=True)
        else:
            raise TimeoutError(f"NeMo server failed to start within {SERVER_STARTUP_TIMEOUT}s")

    def stop(self):
        """Stop the model server."""
        self.shutdown_event.set()
        self.request_queue.put(_SHUTDOWN)

        if self.process and self.process.is_alive():
            self.process.join(timeout=10)
            if self.process.is_alive():
                self.process.terminate()

        # Cleanup PID files
        for f in [_SERVER_PID_FILE, _SERVER_READY_FILE]:
            if f.exists():
                f.unlink()

        print("  [NeMo Server] Stopped.", flush=True)

    def is_healthy(self):
        """
        Send a health-check ping to the server and wait for a response.
        Returns True if the server responds within HEALTH_CHECK_TIMEOUT seconds.
        """
        if not self.process or not self.process.is_alive():
            return False

        try:
            # Drain any stale health responses
            while not self._health_queue.empty():
                try:
                    self._health_queue.get_nowait()
                except Exception:
                    break

            # Send health check request
            self.request_queue.put(("__health__", _HEALTH_CHECK))

            # Wait for response
            response = self._health_queue.get(timeout=HEALTH_CHECK_TIMEOUT)
            return response.get("health", False)
        except Exception:
            return False

    def ensure_alive(self):
        """
        Check if the server is healthy. If not, attempt to restart it.
        Returns True if server is alive (or was successfully restarted).
        Returns False if all restart attempts have been exhausted.
        """
        if self.is_healthy():
            return True

        if self._restart_count >= MAX_RESTARTS:
            print(f"  [NeMo Server] ❌ Max restarts ({MAX_RESTARTS}) reached. Giving up on auto-restart.", flush=True)
            return False

        self._restart_count += 1
        print(f"  [NeMo Server] ⚠️  Server unresponsive. Auto-restarting... (attempt {self._restart_count}/{MAX_RESTARTS})", flush=True)

        try:
            self.stop()
        except Exception:
            pass

        # Brief pause before restart
        time.sleep(2)

        try:
            # Create fresh events and queues
            self.ready_event = multiprocessing.Event()
            self.shutdown_event = multiprocessing.Event()
            self.start()
            print(f"  [NeMo Server] ✅ Restart successful (attempt {self._restart_count})", flush=True)
            return True
        except Exception as e:
            print(f"  [NeMo Server] ❌ Restart failed: {e}", flush=True)
            return False

    def create_client(self, worker_id=None):
        """Create a client for a worker process."""
        if worker_id is None:
            worker_id = f"worker_{os.getpid()}"
        
        response_queue = self._manager.Queue()
        self.response_queues[worker_id] = response_queue
        
        return NemoModelClient(
            request_queue=self.request_queue,
            response_queue=response_queue,
            worker_id=worker_id
        )


class NemoModelClient:
    """
    Client used by worker processes to request log-probs from the shared model.
    """

    def __init__(self, request_queue, response_queue, worker_id):
        self.request_queue = request_queue
        self.response_queue = response_queue
        self.worker_id = worker_id

    def get_log_probs(self, wav_path, timeout=CLIENT_REQUEST_TIMEOUT):
        """
        Send a WAV file to the server and get back log-probabilities.

        Returns:
            tuple: (log_probs_np, audio_duration, vocab_info) or raises Exception
        """
        self.request_queue.put((self.worker_id, wav_path))

        try:
            response = self.response_queue.get(timeout=timeout)
        except Exception:
            raise TimeoutError(f"NeMo server did not respond within {timeout}s")

        if response["status"] == "error":
            raise RuntimeError(f"NeMo server error: {response['error']}")

        return response["log_probs"], response["audio_duration"], response["vocab_info"]


def is_server_running():
    """Check if a NeMo model server is currently running."""
    return _SERVER_READY_FILE.exists() and _SERVER_PID_FILE.exists()

"""
NeMo Model Server — Shared Model for Parallel Workers
======================================================
Loads the NeMo Hindi CTC model ONCE in a dedicated process and serves
log-probability requests to all workers via multiprocessing queues.

This saves ~500 MB RAM per worker since the model is only loaded once
instead of once per parallel worker.

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


# Sentinel values
_SHUTDOWN = "__SHUTDOWN__"
_SERVER_PID_FILE = Path(__file__).parent / "output_song" / ".nemo_server.pid"
_SERVER_READY_FILE = Path(__file__).parent / "output_song" / ".nemo_server.ready"


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
    """

    def __init__(self):
        self.request_queue = multiprocessing.Manager().Queue()
        self._manager = multiprocessing.Manager()
        self.response_queues = self._manager.dict()
        self.ready_event = multiprocessing.Event()
        self.shutdown_event = multiprocessing.Event()
        self.process = None

    def start(self):
        """Start the model server process."""
        print("  [NeMo Server] Starting...", flush=True)

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

        # Wait for model to load
        print("  [NeMo Server] Waiting for model to load...", flush=True)
        self.ready_event.wait(timeout=120)

        if self.ready_event.is_set():
            _SERVER_READY_FILE.touch()
            print("  [NeMo Server] Ready!", flush=True)
        else:
            raise TimeoutError("NeMo server failed to start within 120s")

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

    def get_log_probs(self, wav_path, timeout=120):
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

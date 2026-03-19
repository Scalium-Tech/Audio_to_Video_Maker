"""
upload_queue.py — Background Upload Queue
==========================================
Runs rclone uploads in a separate daemon thread so that
rendering continues unblocked while videos are uploaded.

Usage in batch_processor.py:
    queue = UploadQueue(delivery_folder, gdrive_remote, gdrive_folder)
    queue.start()
    ...
    queue.enqueue(song_name)     # non-blocking
    ...
    queue.drain()                # blocks until all uploads done
    queue.stop()
"""

import csv
import hashlib
import json
import os
import time
import shutil
import queue
import threading
import subprocess
from pathlib import Path
from datetime import datetime

from master_song_catalog import song_output_relpath

try:
    from song_state import set_stage as _set_stage
    _HAS_SONG_STATE = True
except ImportError:
    _HAS_SONG_STATE = False

try:
    from failure_evidence import save_error_log, save_error_context
    _HAS_EVIDENCE = True
except ImportError:
    _HAS_EVIDENCE = False


_STOP_SENTINEL = "__STOP__"
MANIFEST_PATH = Path(__file__).parent.parent / "manifest.json"


class UploadQueue:
    """
    Background upload queue that drains completed videos to Google Drive
    (via rclone) without blocking the main render pipeline.
    
    If rclone is not configured, falls back to copying to a local
    delivery folder.
    """

    def __init__(self, output_folder, delivery_folder, gdrive_remote=None, gdrive_folder=None, benchmark_csv=None):
        """
        Args:
            output_folder: Path to output_song/ where rendered videos live
            delivery_folder: Local fallback delivery folder
            gdrive_remote: rclone remote name (e.g., "bhajan_drive")
            gdrive_folder: Google Drive folder path
            benchmark_csv: Optional Path to benchmark.csv for upload-time writeback
        """
        self._output_folder = Path(output_folder)
        self._delivery_folder = Path(delivery_folder)
        self._gdrive_remote = gdrive_remote or os.environ.get("GDRIVE_REMOTE", "")
        self._gdrive_folder = gdrive_folder or os.environ.get("GDRIVE_FOLDER", "")
        self._benchmark_csv = Path(benchmark_csv) if benchmark_csv else None
        self._queue = queue.Queue()
        self._thread = None
        self._uploaded = 0
        self._failed = 0
        self._lock = threading.Lock()

    def start(self):
        """Start the background upload thread."""
        self._thread = threading.Thread(target=self._worker, daemon=True, name="upload-queue")
        self._thread.start()
        print("  📤 Upload queue started (background thread)", flush=True)

    def enqueue(self, song_name):
        """Add a song to the upload queue (non-blocking)."""
        self._queue.put(song_name)

    def drain(self):
        """Block until all enqueued uploads are complete."""
        self._queue.join()
        with self._lock:
            print(f"  📤 Upload queue drained: {self._uploaded} uploaded, {self._failed} failed", flush=True)

    def stop(self):
        """Signal the worker thread to stop and wait for it."""
        self._queue.put(_STOP_SENTINEL)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=30)

    @property
    def stats(self):
        with self._lock:
            return {"uploaded": self._uploaded, "failed": self._failed}

    def _worker(self):
        """Background worker that processes upload items."""
        while True:
            try:
                song_name = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if song_name == _STOP_SENTINEL:
                self._queue.task_done()
                break

            try:
                self._upload_one(song_name)
                with self._lock:
                    self._uploaded += 1
            except Exception as e:
                print(f"  📤 ❌ Upload failed for {song_name}: {e}", flush=True)
                with self._lock:
                    self._failed += 1
            finally:
                self._queue.task_done()

    def _upload_one(self, song_name):
        """Upload a single song's directory to Google Drive and cleanup local output."""
        song_relpath = song_output_relpath(song_name)
        song_dir = self._output_folder / song_relpath
        if not song_dir.exists():
            return

        # Ensure we have at least an MP4 before uploading the folder
        mp4_files = list(song_dir.glob("*.mp4"))
        if not mp4_files:
            return
        primary_mp4 = mp4_files[0]

        # Try rclone upload
        if self._gdrive_remote and self._gdrive_folder:
            try:
                # remote_path includes the song-specific subfolder
                remote_relpath = song_relpath.as_posix()
                remote_path = f"{self._gdrive_remote}:{self._gdrive_folder}/{remote_relpath}/"
                t0 = time.time()
                copy_result = subprocess.run(
                    ["rclone", "copy", str(song_dir), remote_path,
                     "--transfers", "1", "--retries", "3", "--low-level-retries", "10"],
                    capture_output=True, text=True, timeout=300
                )
                
                if copy_result.returncode != 0:
                    print(f"  📤 ❌ rclone copy failed for {song_name}: {copy_result.stderr[:150]}", flush=True)
                    if _HAS_EVIDENCE:
                        try:
                            save_error_log(song_name, "drive_upload", f"rclone copy exit code {copy_result.returncode}",
                                           extra_lines=[f"stderr: {copy_result.stderr[:500]}"])
                            save_error_context(song_name, "drive_upload", "rclone copy failed",
                                               command=["rclone", "copy", str(song_dir), remote_path],
                                               extra={"stderr": copy_result.stderr[:1000], "exit_code": copy_result.returncode})
                        except Exception:
                            pass
                    raise RuntimeError(f"rclone copy exit code {copy_result.returncode}")

                check_result = subprocess.run(
                    ["rclone", "check", str(song_dir), remote_path, "--one-way"],
                    capture_output=True, text=True, timeout=180
                )

                if check_result.returncode != 0:
                    print(
                        f"  📤 ❌ Upload verification failed for {song_name}: "
                        f"{check_result.stderr[:180] or check_result.stdout[:180]}",
                        flush=True,
                    )
                    if _HAS_EVIDENCE:
                        try:
                            save_error_log(song_name, "drive_upload", "rclone check (verification) failed",
                                           extra_lines=[f"stderr: {check_result.stderr[:500]}"])
                            save_error_context(song_name, "drive_upload", "rclone check failed",
                                               command=["rclone", "check", str(song_dir), remote_path, "--one-way"],
                                               extra={"stderr": check_result.stderr[:1000], "stdout": check_result.stdout[:1000]})
                        except Exception:
                            pass
                    raise RuntimeError("rclone check failed")

                elapsed = time.time() - t0
                print(f"  📤 ✅ Uploaded and verified: {song_name} → Google Drive ({elapsed:.1f}s)", flush=True)
                self._update_csv_upload_time(song_name, elapsed)
                self._append_manifest(song_name, primary_mp4, remote_path)

                # Mark drive_uploaded in song_state
                if _HAS_SONG_STATE:
                    try:
                        _set_stage(song_name, "drive_uploaded")
                    except Exception:
                        pass

                # Cleanup local output folder only after verification succeeds
                try:
                    shutil.rmtree(song_dir)
                    parent_dir = song_dir.parent
                    if parent_dir != self._output_folder:
                        try:
                            parent_dir.rmdir()
                        except OSError:
                            pass
                    print(f"  🗑️  Cleaned up local output for {song_name}", flush=True)
                except Exception as e:
                    print(f"  ⚠️  Failed to cleanup {song_name}: {e}", flush=True)
                return
            except Exception as e:
                print(f"  📤 ❌ rclone error for {song_name}: {e}", flush=True)
                raise
        else:
            print(f"  📤 ⚠️  Skipping upload for {song_name} (no rclone config)", flush=True)

    def _append_manifest(self, song_name: str, mp4_path: Path, remote_path: str):
        """Persist a verified upload record in manifest.json at the pipeline root."""
        try:
            file_hash = self._sha256_file(mp4_path)
            duration_sec = self._probe_duration(mp4_path)
            deity = self._extract_deity(song_name)
            metadata = self._read_metadata(mp4_path.parent / "metadata.json")
            workspace = metadata.get("master_sheet") or mp4_path.parent.parent.name

            manifest = {}
            if MANIFEST_PATH.exists():
                try:
                    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
                except Exception:
                    manifest = {}

            manifest[song_name] = {
                "workspace": workspace,
                "drive_path": remote_path,
                "sha256": file_hash,
                "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "size_mb": round(mp4_path.stat().st_size / (1024 * 1024), 2),
                "duration_sec": duration_sec,
                "deity": metadata.get("deity", deity),
                "channel": metadata.get("channel_key", metadata.get("channel_name", "")),
                "playlist": metadata.get("playlist", ""),
                "style_variant": metadata.get("style_variant", ""),
                "style_variant_label": metadata.get("style_variant_label", ""),
            }

            tmp_path = MANIFEST_PATH.with_suffix(".json.tmp")
            tmp_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_path.replace(MANIFEST_PATH)
        except Exception as e:
            print(f"  ⚠️  Could not update manifest for {song_name}: {e}", flush=True)

    def _read_metadata(self, metadata_path: Path) -> dict:
        if not metadata_path.exists():
            return {}
        try:
            return json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _sha256_file(self, file_path: Path) -> str:
        digest = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _probe_duration(self, file_path: Path) -> float:
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", str(file_path)
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip():
                return round(float(result.stdout.strip()), 2)
        except Exception:
            pass
        return 0.0

    def _extract_deity(self, song_name: str) -> str:
        prefixes = {"lord", "shri", "sri", "maa", "mata", "devi", "bhagwan", "bhagvan", "prabhu", "baba", "goddess"}
        parts = [p for p in song_name.lower().split("_") if p and not p.isdigit()]
        for part in parts:
            if part not in prefixes:
                return part
        return parts[0] if parts else "general"

    def _update_csv_upload_time(self, song_name, elapsed_secs):
        """Write GDrive Upload time back into the last matching row in benchmark.csv."""
        if not self._benchmark_csv or not self._benchmark_csv.exists():
            return
        try:
            with open(self._benchmark_csv, newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))
            if len(rows) < 2:
                return
            headers = rows[0]
            try:
                name_col = headers.index("Song Name")
                upload_col = headers.index("GDrive Upload")
            except ValueError:
                return  # column not found — old format
            # Find last data row matching this song
            for i in range(len(rows) - 1, 0, -1):
                if rows[i][name_col] == song_name:
                    rows[i][upload_col] = f"{elapsed_secs:.1f}s"
                    break
            # Atomic write via .tmp rename
            tmp = self._benchmark_csv.with_suffix(".csv.tmp")
            with open(tmp, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows(rows)
            tmp.replace(self._benchmark_csv)
        except Exception as e:
            print(f"  ⚠️  Could not update CSV upload time for {song_name}: {e}", flush=True)

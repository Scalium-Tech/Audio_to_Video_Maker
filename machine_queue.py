"""
Machine Queue — File-Based Multi-Machine Song Claiming
=======================================================
Allows multiple machines to process from the same input folder
without duplicating work. Uses atomic file creation for locking.

Usage:
    from machine_queue import MachineQueue
    mq = MachineQueue()
    unclaimed = mq.get_unclaimed(song_list)
    mq.claim("SONG_NAME")
    mq.mark_done("SONG_NAME")
"""

import os
import socket
import time
from pathlib import Path


class MachineQueue:
    """File-based queue for multi-machine coordination."""
    
    QUEUE_DIR = Path("output_song") / ".queue"
    
    def __init__(self):
        self.hostname = socket.gethostname()
        self.QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    
    def claim(self, song_name):
        """Atomically claim a song. Returns True if claimed, False if already taken."""
        claim_path = self.QUEUE_DIR / f"{song_name}.claimed"
        try:
            # O_CREAT | O_EXCL = atomic create, fails if exists
            fd = os.open(str(claim_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{self.hostname}\n{time.time()}\n{os.getpid()}\n".encode())
            os.close(fd)
            return True
        except FileExistsError:
            return False
    
    def release(self, song_name):
        """Release a claim (on failure, so another machine can retry)."""
        claim_path = self.QUEUE_DIR / f"{song_name}.claimed"
        try:
            # Only release if we own the claim
            if claim_path.exists():
                content = claim_path.read_text().strip().split("\n")
                if content and content[0] == self.hostname:
                    claim_path.unlink()
                    return True
        except Exception:
            pass
        return False
    
    def mark_done(self, song_name):
        """Mark a song as completed (rename .claimed → .done)."""
        claim_path = self.QUEUE_DIR / f"{song_name}.claimed"
        done_path = self.QUEUE_DIR / f"{song_name}.done"
        try:
            if claim_path.exists():
                claim_path.rename(done_path)
            else:
                # Mark done even if no claim file
                done_path.write_text(f"{self.hostname}\n{time.time()}\n")
            return True
        except Exception:
            return False
    
    def is_claimed_or_done(self, song_name):
        """Check if a song is claimed by any machine or already done."""
        claim_path = self.QUEUE_DIR / f"{song_name}.claimed"
        done_path = self.QUEUE_DIR / f"{song_name}.done"
        
        if done_path.exists():
            return True
        
        if claim_path.exists():
            # Check for stale claims (> 2 hours old)
            try:
                content = claim_path.read_text().strip().split("\n")
                if len(content) >= 2:
                    claim_time = float(content[1])
                    if time.time() - claim_time > 7200:  # 2 hours
                        print(f"  🧹 Stale claim for {song_name} (>{2}h). Removing.")
                        claim_path.unlink()
                        return False
            except Exception:
                pass
            return True
        
        return False
    
    def get_unclaimed(self, song_names):
        """Filter list to only unclaimed/undone songs."""
        return [s for s in song_names if not self.is_claimed_or_done(s)]
    
    def get_stats(self):
        """Return queue statistics."""
        claimed = len(list(self.QUEUE_DIR.glob("*.claimed")))
        done = len(list(self.QUEUE_DIR.glob("*.done")))
        return {"claimed": claimed, "done": done, "hostname": self.hostname}
    
    def cleanup(self):
        """Remove all claim/done files (for fresh start)."""
        for f in self.QUEUE_DIR.glob("*.claimed"):
            f.unlink()
        for f in self.QUEUE_DIR.glob("*.done"):
            f.unlink()


if __name__ == "__main__":
    mq = MachineQueue()
    stats = mq.get_stats()
    print(f"Machine Queue — {stats['hostname']}")
    print(f"  Claimed: {stats['claimed']}")
    print(f"  Done: {stats['done']}")

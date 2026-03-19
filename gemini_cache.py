"""
Gemini Response Cache — Disk-based caching for Gemini API responses
====================================================================
Caches successful responses keyed by SHA-256 hash of (model + payload).
Skips caching for image generation pool (responses are non-deterministic).

Cache stored in: output_song/.gemini_cache/
"""

import json
import hashlib
import time
from pathlib import Path


CACHE_DIR = Path("output_song") / ".gemini_cache"


class GeminiCache:
    """Thread-safe disk cache for Gemini API responses."""

    def __init__(self, cache_dir=None):
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._hits = 0
        self._misses = 0

    def _make_key(self, model_name, payload):
        """Create a deterministic cache key from model + payload."""
        raw = json.dumps({"m": model_name, "p": payload}, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def get(self, model_name, payload):
        """
        Look up cached response. Returns cached response dict or None.
        """
        key = self._make_key(model_name, payload)
        cache_file = self.cache_dir / f"{key}.json"

        if cache_file.exists():
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    cached = json.load(f)
                # Check TTL (7 days)
                if time.time() - cached.get("cached_at", 0) < 7 * 86400:
                    self._hits += 1
                    return cached.get("response")
                else:
                    cache_file.unlink()  # Expired
            except Exception:
                pass

        self._misses += 1
        return None

    def put(self, model_name, payload, response):
        """Store a successful response in the cache."""
        key = self._make_key(model_name, payload)
        cache_file = self.cache_dir / f"{key}.json"

        try:
            entry = {
                "model": model_name,
                "cached_at": time.time(),
                "response": response
            }
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(entry, f, ensure_ascii=False)
        except Exception:
            pass  # Cache write failures are non-fatal

    def stats(self):
        """Return cache hit/miss statistics."""
        total = self._hits + self._misses
        hit_rate = (self._hits / total * 100) if total > 0 else 0
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(hit_rate, 1),
            "cache_files": len(list(self.cache_dir.glob("*.json"))) if self.cache_dir.exists() else 0
        }

    def clear(self):
        """Remove all cached responses."""
        if self.cache_dir.exists():
            for f in self.cache_dir.glob("*.json"):
                f.unlink()


# Global singleton
_cache = GeminiCache()


def get_cache():
    """Get the global cache instance."""
    return _cache

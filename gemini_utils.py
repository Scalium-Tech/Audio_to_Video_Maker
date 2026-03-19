import os
import time
import random
import threading
import requests
import json
from pathlib import Path

# =================================================================
# GEMINI TRAFFIC CONTROLLER (V2)
# =================================================================
# - Per-endpoint key pools (alignment, image, punctuation)
# - Per-key cooldown tracking (skip keys that recently hit 429)
# - Thread-safe round-robin rotation
# - Global concurrency limiter via semaphore
# - Token usage tracking per pool
# =================================================================

# ── Load config from config.yaml (fall back to defaults) ──
def _load_gemini_config():
    """Load gemini-specific settings from config.yaml."""
    defaults = {"concurrency_limit": 3, "key_cooldown_seconds": 60, "request_timeout": 180, "max_retries": 5}
    try:
        import yaml
        cfg_path = Path(__file__).parent / "config.yaml"
        if cfg_path.exists():
            with open(cfg_path) as f:
                cfg = yaml.safe_load(f) or {}
            gemini_cfg = cfg.get("gemini", {})
            return {k: gemini_cfg.get(k, v) for k, v in defaults.items()}
    except Exception:
        pass
    return defaults

_CFG = _load_gemini_config()
GEMINI_CONCURRENCY_LIMIT = _CFG["concurrency_limit"]
_gemini_semaphore = threading.Semaphore(GEMINI_CONCURRENCY_LIMIT)

# Per-key cooldown duration (seconds) after hitting 429
KEY_COOLDOWN_SECONDS = _CFG["key_cooldown_seconds"]
GEMINI_REQUEST_TIMEOUT = _CFG["request_timeout"]
GEMINI_MAX_RETRIES = _CFG["max_retries"]


class _KeyPoolManager:
    """
    Manages multiple named pools of API keys with per-key cooldown tracking.
    
    Pools:
      - "alignment": for gemini_align.py calls
      - "image": for generate_background.py calls  
      - "punctuation": for lyrics_extractor.py calls
      - "default": fallback pool for any unspecified usage
    
    Key loading priority per pool:
      1. GEMINI_KEYS_<POOL> env var (e.g., GEMINI_KEYS_ALIGNMENT=k1,k2)
      2. GEMINI_API_KEYS env var (shared pool)
      3. GEMINI_API_KEY env var (single key)
    
    Per-key cooldown:
      When a key hits 429, it's marked with a cooldown timestamp.
      get_next_key() skips cooled-down keys, picking the next available one.
      If ALL keys are cooling, it sleeps until the shortest cooldown expires.
    """

    def __init__(self):
        self._pools = {}        # pool_name -> list of keys
        self._indices = {}      # pool_name -> current round-robin index
        self._cooldowns = {}    # key -> cooldown_until_timestamp
        self._lock = threading.Lock()
        self._loaded_pools = set()

    def _load_pool(self, pool_name):
        """Lazy-load keys for a specific pool from environment."""
        if pool_name in self._loaded_pools:
            return
        with self._lock:
            if pool_name in self._loaded_pools:
                return

            keys = []

            # 1. Try pool-specific env var: GEMINI_KEYS_ALIGNMENT, etc.
            pool_env = f"GEMINI_KEYS_{pool_name.upper()}"
            pool_val = os.environ.get(pool_env, "")
            if pool_val.strip():
                keys = [k.strip() for k in pool_val.split(",") if k.strip()]

            # 2. Fallback to shared pool
            if not keys:
                shared = os.environ.get("GEMINI_API_KEYS", "")
                if shared.strip():
                    keys = [k.strip() for k in shared.split(",") if k.strip()]

            # 3. Fallback to single key
            if not keys:
                single = os.environ.get("GEMINI_API_KEY", "")
                if single.strip():
                    keys = [single.strip()]

            self._pools[pool_name] = keys
            self._indices[pool_name] = 0
            self._loaded_pools.add(pool_name)

            if keys:
                source = pool_env if pool_val.strip() else "shared"
                if len(keys) > 1:
                    print(f"  🔑 Gemini [{pool_name}]: {len(keys)} keys loaded ({source})", flush=True)

    def get_next_key(self, pool="default", explicit_key=None):
        """
        Get the next available API key from the named pool via round-robin.
        Skips keys that are in cooldown. If all keys are cooling, waits.
        
        Args:
            pool: Pool name ("alignment", "image", "punctuation", "default")
            explicit_key: If provided, use this key directly (bypass rotation)
        
        Returns:
            API key string, or None if no keys available
        """
        if explicit_key:
            return explicit_key

        self._load_pool(pool)
        keys = self._pools.get(pool, [])
        if not keys:
            return None

        now = time.time()

        with self._lock:
            # Try to find a non-cooled-down key
            n = len(keys)
            for _ in range(n):
                idx = self._indices[pool] % n
                self._indices[pool] += 1
                key = keys[idx]
                cooldown_until = self._cooldowns.get(key, 0)
                if now >= cooldown_until:
                    return key

        # All keys are in cooldown — wait for the shortest one
        with self._lock:
            min_wait = min(
                self._cooldowns.get(k, 0) - now
                for k in keys
            )
        min_wait = max(0.1, min_wait)
        print(f"  ⏸️  All keys in [{pool}] cooling down. Waiting {min_wait:.1f}s...", flush=True)
        time.sleep(min_wait)

        # After waiting, return the next key (cooldown should be expired)
        with self._lock:
            idx = self._indices[pool] % len(keys)
            self._indices[pool] += 1
            return keys[idx]

    def mark_rate_limited(self, key, cooldown_seconds=KEY_COOLDOWN_SECONDS):
        """Mark a key as rate-limited with a cooldown period."""
        with self._lock:
            self._cooldowns[key] = time.time() + cooldown_seconds

    def get_key_count(self, pool="default"):
        """Get the number of keys in a pool."""
        self._load_pool(pool)
        return len(self._pools.get(pool, []))


# Global singleton
_pool_manager = _KeyPoolManager()


# =================================================================
# TOKEN USAGE TRACKER
# =================================================================
class _TokenTracker:
    """Thread-safe tracker for Gemini API token consumption per pool."""

    def __init__(self):
        self._lock = threading.Lock()
        self._usage = {}  # pool -> {prompt_tokens, candidates_tokens, total_tokens, calls}

    def record(self, pool, usage_metadata):
        """Record token usage from a Gemini response's usageMetadata."""
        if not usage_metadata:
            return
        with self._lock:
            if pool not in self._usage:
                self._usage[pool] = {"prompt_tokens": 0, "candidates_tokens": 0, "total_tokens": 0, "calls": 0}
            entry = self._usage[pool]
            entry["prompt_tokens"] += usage_metadata.get("promptTokenCount", 0)
            entry["candidates_tokens"] += usage_metadata.get("candidatesTokenCount", 0)
            entry["total_tokens"] += usage_metadata.get("totalTokenCount", 0)
            entry["calls"] += 1

    def summary(self):
        """Return a copy of accumulated token usage."""
        with self._lock:
            grand_total = sum(e["total_tokens"] for e in self._usage.values())
            return {"per_pool": dict(self._usage), "grand_total_tokens": grand_total}


_token_tracker = _TokenTracker()


def get_token_summary():
    """Public API: get accumulated Gemini token usage for reporting."""
    return _token_tracker.summary()


def call_gemini_api(model_name, payload, api_key=None, max_retries=None, is_predict=False, pool="default"):
    """
    Robust Gemini API caller with:
    1. Per-endpoint key pools (alignment, image, punctuation)
    2. Per-key cooldown tracking (skip keys that recently hit 429)
    3. Global Concurrency Limit (via Semaphore)
    4. Exponential Backoff + Jitter for retryable errors
    5. Disk-based response caching (skips 'image' pool)
    
    Args:
        pool: Key pool name ("alignment", "image", "punctuation", "default")
    """
    if max_retries is None:
        max_retries = GEMINI_MAX_RETRIES
    headers = {"Content-Type": "application/json"}

    # ── Cache check (skip for image generation — non-deterministic) ──
    use_cache = pool != "image"
    if use_cache:
        try:
            from gemini_cache import get_cache
            cache = get_cache()
            cached = cache.get(model_name, payload)
            if cached is not None:
                return cached
        except ImportError:
            use_cache = False
        except Exception:
            pass

    for attempt in range(max_retries):
        # Get the next key from the pool (round-robin, skipping cooled keys)
        key = _pool_manager.get_next_key(pool=pool, explicit_key=api_key)
        if not key:
            return {"status": "error", "error": "No GEMINI_API_KEY(S) found"}

        # Build URL
        if is_predict:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:predict?key={key}"
        else:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={key}"

        # ── Step 1: Wait for a slot in the Traffic Controller ──
        with _gemini_semaphore:
            try:
                # Small jittered delay to prevent simultaneous bursts
                time.sleep(random.uniform(0.1, 0.4))

                response = requests.post(url, headers=headers, json=payload, timeout=GEMINI_REQUEST_TIMEOUT)

                # SUCCESS
                if response.status_code == 200:
                    resp_data = response.json()
                    # Track token usage
                    usage = resp_data.get("usageMetadata")
                    _token_tracker.record(pool, usage)
                    result = {"status": "success", "data": resp_data}
                    # Cache successful response
                    if use_cache:
                        try:
                            cache.put(model_name, payload, result)
                        except Exception:
                            pass
                    return result

                # RATE LIMIT (429) — mark key with cooldown, try next
                if response.status_code == 429:
                    _pool_manager.mark_rate_limited(key)
                    key_count = _pool_manager.get_key_count(pool)
                    pool_info = f" [{pool}]" if pool != "default" else ""
                    key_info = f" (key cooled 60s, {key_count} in pool)" if key_count > 1 else ""
                    wait_time = (2 ** attempt) + random.uniform(0.5, 1.5)
                    print(f"  ⚠️  429 on {model_name}{pool_info}{key_info}. Retry in {wait_time:.1f}s (attempt {attempt+1}/{max_retries})", flush=True)
                    pass  # semaphore released at end of with block

                # SERVER ERROR (5xx)
                elif response.status_code in [500, 502, 503, 504]:
                    print(f"  ⚠️  Gemini Server Error ({response.status_code}). Retrying...", flush=True)
                    pass

                # PERMANENT ERROR
                else:
                    return {
                        "status": "error",
                        "code": response.status_code,
                        "error": response.text[:200]
                    }

            except (requests.exceptions.RequestException, Exception) as e:
                print(f"  ⚠️  Gemini Connection Error: {e}. Retrying...", flush=True)
                pass

        # ── Step 2: Exponential Backoff (after releasing semaphore) ──
        wait_time = (2 ** attempt) + random.uniform(0.5, 1.5)
        time.sleep(wait_time)

    return {"status": "error", "error": f"Max retries ({max_retries}) exceeded for {model_name}"}


def get_gemini_text(response_json):
    """Helper to extract and join all text parts from a standard Gemini response."""
    try:
        candidates = response_json.get('candidates', [])
        if not candidates:
            return None

        parts = candidates[0].get('content', {}).get('parts', [])
        if not parts:
            return None

        full_text = "".join([p.get('text', '') for p in parts])
        return full_text
    except Exception:
        return None


def get_cache_stats():
    """Get Gemini response cache hit/miss statistics."""
    try:
        from gemini_cache import get_cache
        return get_cache().stats()
    except ImportError:
        return {"hits": 0, "misses": 0, "hit_rate": 0, "cache_files": 0}

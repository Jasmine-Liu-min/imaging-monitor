"""Central place for tunables that used to be scattered magic numbers.

Keeping them here makes behaviour easy to explain and adjust without grepping
the whole codebase. Network/AI knobs can still be overridden via env vars in the
respective modules; these are the defaults.
"""
from __future__ import annotations

# --- Fetching ---
MAX_ITEMS_PER_SOURCE = 30        # cap RSS/HTML items pulled from a single feed
HTTP_TIMEOUT_SECONDS = 25        # per-request timeout for source fetches
HTTP_RETRY_ATTEMPTS = 2          # extra attempts on retryable network errors
MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5MB ceiling to avoid memory blowups

# --- Text truncation (chars) ---
SUMMARY_FETCH_LEN = 600          # candidate.summary length from a feed
CONTENT_FETCH_LEN = 1500         # candidate.content length from a feed
AI_CONTENT_LEN = 2200            # per-candidate content sent to the AI
TITLE_LEN = 180
TITLE_ORIGINAL_LEN = 220
SUMMARY_LEN = 240
DETAIL_LEN = 180
INSIGHT_LEN = 280

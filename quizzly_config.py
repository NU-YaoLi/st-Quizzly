"""
Central configuration constants for Quizzly.

Single source of truth for caps (questions, web slots, file fingerprints), the
LLM model id, Supabase URL, answer-letter ordering, and per-1K-token pricing
used by the analytics dashboard. Imported by both backend and frontend modules.
"""

MIN_QUESTIONS = 3
MAX_WEB_URL_SLOTS = 5

# Supabase-backed daily generation cap (per salted IP hash, UTC day).
DAILY_GENERATION_LIMIT = 5

# Supabase project URL (public). The service role key stays in Streamlit secrets
# (`SUPABASE_SERVICE_ROLE_KEY`) and is required for server-side rate limit + usage logging.
SUPABASE_URL = "https://udbxyrssdjndmvuakhgb.supabase.co"

# -------------------
# Model configuration
# -------------------
# Single source of truth for the model used across all backend steps.
QUIZZLY_MODEL = "gpt-5-mini"

# Website sizing (heuristic)
WEB_CHARS_PER_PAGE = 2500
WEB_TEXT_PER_URL_CAP = 12000

# Hard cap to avoid extremely large generations
MAX_QUESTIONS_CAP = 50

# -------------------------------
# Adaptive PDF content pipeline
# -------------------------------
# Per-call input-token budget. Stays below the model's 272k cap with headroom for
# the system prompt and reasoning output. Used by the pre-flight planner.
INPUT_TOKEN_BUDGET_PER_CALL = 200_000

# Per-page text-density classification (chars of extractable text per PDF page).
# >= RICH      → text-only (cheapest, no image rendering)
# < SPARSE     → vision-needed (rendered as low-detail image)
# in between   → mixed (text + low-detail image)
PDF_TEXT_RICH_THRESHOLD = 1000
PDF_TEXT_SPARSE_THRESHOLD = 200

# Render DPI used when the planner decides a page needs to be sent as an image.
# - LOW: ~85 vision tokens per page (model still reads big text & shapes).
# - HIGH: ~25k vision tokens per page (used sparingly for chart-critical pages).
PDF_RENDER_DPI_LOW = 110
PDF_RENDER_DPI_HIGH = 200

# Hard cap on pages allowed to use HIGH detail across one request, to keep the
# total token cost predictable even on chart-heavy textbooks.
MAX_HIGH_DETAIL_PAGES = 4

# Heuristic chars-per-token ratio for English text. Used by the planner only;
# the actual OpenAI tokenizer is not invoked (we cannot afford ~1s of tiktoken
# overhead on the UI thread). 4.0 is the standard rule of thumb.
CHARS_PER_TOKEN = 4.0

# Vision-token estimates used by the planner.
VISION_TOKENS_LOW_DETAIL = 85
VISION_TOKENS_HIGH_DETAIL = 25_000

# Duplicate detection for uploaded files (fingerprint first N bytes + size)
FILE_FINGERPRINT_BYTES = 256 * 1024

# Cache for website fetch in UI
WEB_FETCH_CACHE_TTL_SECS = 600

ANSWER_LETTERS = ["A", "B", "C", "D"]

# Optional pricing for estimating quiz cost.
# Fill in with your model pricing (USD per 1K tokens) if you want cost estimates.
MODEL_PRICING_USD_PER_1K: dict[str, dict[str, float]] = {
    # Pricing provided by user (USD per 1M tokens):
    # - input:  $0.25 / 1M  => $0.00025 / 1K
    # - cached: $0.025 / 1M => $0.000025 / 1K
    # - output: $2.00 / 1M  => $0.00200 / 1K
    "gpt-5-mini": {"input": 0.00025, "cached_input": 0.000025, "output": 0.00200},
    # Pricing provided by user (USD per 1M tokens):
    # - input:  $0.75 / 1M  => $0.00075 / 1K
    # - cached: $0.075 / 1M => $0.000075 / 1K
    # - output: $4.50 / 1M  => $0.00450 / 1K
    "gpt-5.4-mini": {"input": 0.00075, "cached_input": 0.000075, "output": 0.00450},
}


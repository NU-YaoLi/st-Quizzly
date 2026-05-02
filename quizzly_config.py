MIN_QUESTIONS = 3
MAX_WEB_URL_SLOTS = 5

# Supabase-backed daily generation cap (per salted IP hash, UTC day).
DAILY_GENERATION_LIMIT = 3

# Supabase project URL (public). Keys stay in Streamlit secrets only:
# - SUPABASE_ANON_KEY — optional / reserved for future client-side Supabase use
# - SUPABASE_SERVICE_ROLE_KEY — required for server-side rate limit (insert/select)
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


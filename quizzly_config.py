MIN_QUESTIONS = 3
MAX_WEB_URL_SLOTS = 5

# Website sizing (heuristic)
WEB_CHARS_PER_PAGE = 2500
WEB_TEXT_PER_URL_CAP = 12000

# Hard cap to avoid extremely large generations
MAX_QUESTIONS_CAP = 50
# Additional cap to keep question count reasonable per source
MAX_QUESTIONS_PER_SOURCE = 30

# Duplicate detection for uploaded files (fingerprint first N bytes + size)
FILE_FINGERPRINT_BYTES = 256 * 1024

# Cache for website fetch in UI
WEB_FETCH_CACHE_TTL_SECS = 600

ANSWER_LETTERS = ["A", "B", "C", "D"]

# Optional pricing for estimating quiz cost.
# Fill in with your model pricing (USD per 1K tokens) if you want cost estimates.
# Example:
# MODEL_PRICING_USD_PER_1K = {
#   "gpt-5.4-mini": {"prompt": 0.0, "completion": 0.0},
#   "gpt-5-mini": {"prompt": 0.0, "completion": 0.0},
# }
MODEL_PRICING_USD_PER_1K: dict[str, dict[str, float]] = {
    # Pricing provided by user (USD per 1M tokens):
    # - input:  $0.25 / 1M  => $0.00025 / 1K
    # - cached: $0.025 / 1M => $0.000025 / 1K
    # - output: $2.00 / 1M  => $0.00200 / 1K
    "gpt-5-mini": {"input": 0.00025, "cached_input": 0.000025, "output": 0.00200},
}


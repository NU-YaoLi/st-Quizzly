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


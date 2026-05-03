"""
Structured fields logged per successful quiz generation (Supabase `quiz_generation_usage`).
"""

from dataclasses import dataclass, fields

# Avoid ``from __future__ import annotations`` here: on Python 3.14 it can break
# ``@dataclass`` module resolution (sys.modules lookup during class body processing).


@dataclass()
class QuizGenerationUsageFields:
    """Optional analytics columns; omitted keys can be stored as NULL."""

    estimated_cost_usd: float | None = None
    num_questions: int | None = None
    generation_mode: str | None = None  # "full" | "fast"
    material_source: str | None = None  # "upload_files" | "website_links"
    material_quantity: int | None = None
    upload_total_bytes: int | None = None
    web_text_chars: int | None = None
    ext_input_tokens: int | None = None
    ext_cached_input_tokens: int | None = None
    ext_output_tokens: int | None = None
    gen_input_tokens: int | None = None
    gen_cached_input_tokens: int | None = None
    gen_output_tokens: int | None = None
    vrf_input_tokens: int | None = None
    vrf_cached_input_tokens: int | None = None
    vrf_output_tokens: int | None = None
    generation_duration_sec: float | None = None

    def as_insert_dict(self, user_ip_id: str) -> dict:
        row = {"user_ip_id": user_ip_id}
        for f in fields(self):
            row[f.name] = getattr(self, f.name)
        return row


def token_triple_from_breakdown(bd: dict | None) -> tuple[int | None, int | None, int | None]:
    """Maps `_estimate_cost_precise` breakdown dict to (input, cached_input, output)."""
    if not bd:
        return None, None, None
    return (
        bd.get("input_tokens"),
        bd.get("cached_input_tokens"),
        bd.get("output_tokens"),
    )

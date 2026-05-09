"""
Adaptive PDF content pipeline for Quizzly.

Replaces the legacy ``client.files.create`` + ``type:"file"`` upload path with a
local-first, per-page-classified pipeline that decides — silently, without any
user toggle — the cheapest sufficient representation of each page before any
LLM call is made.

Why this exists
---------------
OpenAI's chat-completions ``type:"file"`` transport rasterizes every PDF page
through the vision tokenizer (~25k tokens per high-detail page). A 40-page
PDF can balloon to ~1M input tokens regardless of how much extractable text
it contains, blowing past the model's input-token cap. This module computes
the actual content the model needs page-by-page and ships it as a tight mix
of ``type:"text"`` + ``type:"image_url"`` parts.

Pipeline
--------
1. ``analyze_pdf(path)``  – per-page text + image inventory (PyPDF2, fast).
2. ``plan_content_strategy(infos, budget)``  – classify each page
   (TEXT_RICH / MIXED / VISUAL_HEAVY / EMPTY), pick cheapest sufficient
   representation, and degrade to fit the budget. Returns a ``ContentPlan``.
3. ``build_content_parts(plan)``  – materialize the OpenAI content parts.
   Renders images only for the (typically few) pages that need vision, and
   only after the plan is approved by the budget check.

Public API
----------
- ``ContentPlan`` (dataclass)
- ``TooLargeError`` (custom exception)
- ``build_content_parts_for_files(paths, budget) -> (parts, est_tokens, report)``
  – the convenience wrapper called from the frontend.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Literal

import PyPDF2

from quizzly_config import (
    CHARS_PER_TOKEN,
    INPUT_TOKEN_BUDGET_PER_CALL,
    MAX_HIGH_DETAIL_PAGES,
    PDF_RENDER_DPI_HIGH,
    PDF_RENDER_DPI_LOW,
    PDF_TEXT_RICH_THRESHOLD,
    PDF_TEXT_SPARSE_THRESHOLD,
    VISION_TOKENS_HIGH_DETAIL,
    VISION_TOKENS_LOW_DETAIL,
)

PageStrategy = Literal["text", "text+low", "low", "high", "skip"]


class TooLargeError(Exception):
    """Raised when no plan fits the input-token budget, even after degradation."""


@dataclass
class PageInfo:
    """Lightweight per-page facts gathered without rendering anything."""

    index: int  # 0-based
    text: str
    char_count: int
    word_count: int
    image_count: int
    has_figure_caption: bool  # heuristic: text contains "Figure N" / "Fig. N"

    @property
    def is_text_rich(self) -> bool:
        return self.char_count >= PDF_TEXT_RICH_THRESHOLD

    @property
    def is_text_sparse(self) -> bool:
        return self.char_count < PDF_TEXT_SPARSE_THRESHOLD

    @property
    def is_empty(self) -> bool:
        return self.char_count == 0 and self.image_count == 0


@dataclass
class PagePlan:
    """One page's chosen strategy + estimated token cost."""

    page: PageInfo
    strategy: PageStrategy
    estimated_tokens: int


@dataclass
class ContentPlan:
    """Full plan for one PDF: per-page strategies + totals + report."""

    file_path: str
    file_hash: str
    page_plans: list[PagePlan]
    estimated_tokens: int
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 1. Analyze
# ---------------------------------------------------------------------------


def _file_hash(path: str) -> str:
    """Stable short hash for cache keying. First 256 KB is enough in practice."""
    h = hashlib.sha1()
    try:
        with open(path, "rb") as f:
            h.update(f.read(256 * 1024))
        h.update(str(os.path.getsize(path)).encode())
    except Exception:
        h.update(path.encode("utf-8", errors="ignore"))
    return h.hexdigest()[:16]


def _count_page_images(page) -> int:
    """Best-effort embedded-image count for a PyPDF2 page; never raises."""
    try:
        resources = page.get("/Resources")
        if not resources:
            return 0
        xobj = resources.get("/XObject")
        if not xobj:
            return 0
        xobj = xobj.get_object()
        count = 0
        for k in xobj.keys():
            try:
                if xobj[k].get_object().get("/Subtype") == "/Image":
                    count += 1
            except Exception:
                continue
        return count
    except Exception:
        return 0


def _detect_figure_caption(text: str) -> bool:
    """Heuristic for chart-critical pages: 'Figure 3.1', 'Fig. 4', 'Table 2'."""
    if not text:
        return False
    lo = text.lower()
    for marker in ("figure ", "fig. ", "fig.", "table "):
        if marker in lo:
            return True
    return False


def analyze_pdf(path: str) -> list[PageInfo]:
    """
    Read a PDF and return per-page facts. No rendering, no API calls.

    Empirically ~10-30ms per page; safely under 3s for typical (≤100 page) PDFs.
    """
    out: list[PageInfo] = []
    with open(path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for i, page in enumerate(reader.pages):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            text = text.strip()
            out.append(
                PageInfo(
                    index=i,
                    text=text,
                    char_count=len(text),
                    word_count=len(text.split()),
                    image_count=_count_page_images(page),
                    has_figure_caption=_detect_figure_caption(text),
                )
            )
    return out


# ---------------------------------------------------------------------------
# 2. Plan
# ---------------------------------------------------------------------------


def _initial_strategy(p: PageInfo) -> PageStrategy:
    """Pick the cheapest representation that conveys the page's content."""
    if p.is_empty:
        return "skip"
    if p.is_text_rich:
        # Decorative banners + dense text → text alone is sufficient.
        return "text"
    if p.is_text_sparse:
        # Scanned, image-of-text, or chart with no caption → vision-only.
        return "low"
    # Mixed page (200 ≤ chars < 1000): keep text and add a low-detail image.
    return "text+low"


def _estimate_tokens(p: PageInfo, strategy: PageStrategy) -> int:
    """Token estimate for one page under a given strategy. Heuristic, not exact."""
    text_tokens = max(1, int(p.char_count / CHARS_PER_TOKEN)) if p.char_count else 0
    if strategy == "skip":
        return 0
    if strategy == "text":
        return text_tokens
    if strategy == "low":
        return VISION_TOKENS_LOW_DETAIL
    if strategy == "text+low":
        return text_tokens + VISION_TOKENS_LOW_DETAIL
    if strategy == "high":
        return text_tokens + VISION_TOKENS_HIGH_DETAIL
    return text_tokens


def plan_content_strategy(
    file_path: str,
    pages: list[PageInfo],
    budget_tokens: int = INPUT_TOKEN_BUDGET_PER_CALL,
) -> ContentPlan:
    """
    Decide a per-page strategy that fits within ``budget_tokens``.

    Algorithm:
      1. Initial pick: cheapest sufficient representation per page.
      2. Promote up to ``MAX_HIGH_DETAIL_PAGES`` chart pages (figure-caption +
         visual-heavy) to high-detail, but only while we stay under budget.
      3. If still over budget, demote any high-detail back to low-detail.
      4. If still over budget, drop trailing TEXT_RICH pages until we fit.
      5. If still over budget, raise ``TooLargeError`` with a clear message.
    """
    notes: list[str] = []
    plans: list[PagePlan] = [
        PagePlan(page=p, strategy=_initial_strategy(p), estimated_tokens=0) for p in pages
    ]
    for pp in plans:
        pp.estimated_tokens = _estimate_tokens(pp.page, pp.strategy)

    # 2. Selectively promote chart-critical pages to high-detail (if budget allows).
    chart_candidates = [
        pp for pp in plans
        if pp.strategy in ("low", "text+low")
        and (pp.page.has_figure_caption or pp.page.image_count >= 1 and pp.page.is_text_sparse)
    ]
    chart_candidates = chart_candidates[:MAX_HIGH_DETAIL_PAGES]
    for pp in chart_candidates:
        candidate_tokens = _estimate_tokens(pp.page, "high")
        delta = candidate_tokens - pp.estimated_tokens
        running_total = sum(x.estimated_tokens for x in plans)
        if running_total + delta <= budget_tokens:
            pp.strategy = "high"
            pp.estimated_tokens = candidate_tokens
            notes.append(f"page {pp.page.index + 1}: promoted to high-detail (chart-critical).")

    total = sum(p.estimated_tokens for p in plans)

    # 3. If over budget, undo any high-detail promotions.
    if total > budget_tokens:
        for pp in plans:
            if pp.strategy == "high":
                pp.strategy = "text+low" if pp.page.char_count else "low"
                pp.estimated_tokens = _estimate_tokens(pp.page, pp.strategy)
        total = sum(p.estimated_tokens for p in plans)
        if any(n.startswith("page") for n in notes):
            notes.append("Demoted high-detail pages back to low-detail to fit budget.")

    # 4. If still over, drop trailing TEXT_RICH pages until we fit.
    if total > budget_tokens:
        dropped = 0
        for pp in reversed(plans):
            if total <= budget_tokens:
                break
            if pp.strategy in ("text", "text+low") and pp.page.is_text_rich:
                total -= pp.estimated_tokens
                pp.strategy = "skip"
                pp.estimated_tokens = 0
                dropped += 1
        if dropped:
            notes.append(
                f"Dropped {dropped} trailing text-rich page(s) to fit the {budget_tokens:,}-token budget."
            )

    # 5. Still over budget → unrecoverable.
    if total > budget_tokens:
        raise TooLargeError(
            f"Estimated {total:,} input tokens exceeds the {budget_tokens:,} budget "
            f"even after degradation. Reduce pages, split the PDF, or remove the largest file."
        )

    return ContentPlan(
        file_path=file_path,
        file_hash=_file_hash(file_path),
        page_plans=plans,
        estimated_tokens=total,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# 3. Build content parts
# ---------------------------------------------------------------------------


# Cache: (file_hash, page_index, dpi) -> base64 PNG. Keeps repeat generations
# instant on the same file without rendering twice. Bounded so a long-running
# Streamlit Cloud worker that processes many distinct PDFs doesn't grow the
# cache unbounded (each entry can be 50-200 KB of PNG bytes for low-DPI low-
# detail renders, more for high-detail).
_RENDER_CACHE_MAX_ENTRIES = 256
_RENDER_CACHE: "OrderedDict[tuple[str, int, int], str]" = OrderedDict()


def _render_page_b64_png(path: str, page_index: int, dpi: int, file_hash: str) -> str:
    """Render one PDF page to base64-encoded PNG using PyMuPDF (fitz)."""
    cache_key = (file_hash, page_index, dpi)
    cached = _RENDER_CACHE.get(cache_key)
    if cached is not None:
        # Refresh recency on hit so least-recently-used entries get evicted first.
        _RENDER_CACHE.move_to_end(cache_key)
        return cached

    # Imported lazily so a missing PyMuPDF doesn't break text-only paths.
    import fitz  # type: ignore[import]

    doc = fitz.open(path)
    try:
        page = doc.load_page(page_index)
        # 72 is PDF's native DPI; the matrix scales accordingly.
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        png_bytes = pix.tobytes("png")
    finally:
        doc.close()

    b64 = base64.b64encode(png_bytes).decode("ascii")
    _RENDER_CACHE[cache_key] = b64
    # LRU eviction: drop the oldest entry once we exceed the cap.
    while len(_RENDER_CACHE) > _RENDER_CACHE_MAX_ENTRIES:
        _RENDER_CACHE.popitem(last=False)
    return b64


def _truncate_for_safety(text: str) -> str:
    """Hard cap any single page's text to keep one runaway page from blowing the budget."""
    # ~50k chars ≈ ~12.5k tokens — generous, but bounded.
    HARD_CAP = 50_000
    if len(text) <= HARD_CAP:
        return text
    return text[:HARD_CAP] + "\n[... page truncated for length ...]"


def build_content_parts(plan: ContentPlan) -> list[dict]:
    """
    Materialize one PDF's plan into OpenAI content parts.

    Returns a list of dicts ready to drop into a HumanMessage content array:
      - ``{"type":"text","text": "<page text>"}``
      - ``{"type":"image_url","image_url":{"url":"data:image/png;base64,...", "detail":"low"|"high"}}``
    """
    parts: list[dict] = []
    file_label = os.path.basename(plan.file_path)
    parts.append(
        {
            "type": "text",
            "text": f"<source_file name=\"{file_label}\" pages=\"{len(plan.page_plans)}\" />",
        }
    )

    for pp in plan.page_plans:
        idx_label = f"page {pp.page.index + 1}"
        if pp.strategy == "skip":
            continue

        # Text part for any strategy that includes text.
        if pp.strategy in ("text", "text+low") and pp.page.text:
            parts.append(
                {
                    "type": "text",
                    "text": f"--- {file_label} :: {idx_label} ---\n{_truncate_for_safety(pp.page.text)}",
                }
            )

        # Image part for any strategy that includes vision.
        if pp.strategy in ("text+low", "low", "high"):
            dpi = PDF_RENDER_DPI_HIGH if pp.strategy == "high" else PDF_RENDER_DPI_LOW
            detail = "high" if pp.strategy == "high" else "low"
            try:
                b64 = _render_page_b64_png(plan.file_path, pp.page.index, dpi, plan.file_hash)
            except ImportError:
                # PyMuPDF not installed → fall back to text-only for this page.
                # The plan was built assuming vision was available; surface a note
                # but don't crash the workflow.
                if pp.page.text:
                    parts.append(
                        {
                            "type": "text",
                            "text": (
                                f"--- {file_label} :: {idx_label} (image rendering unavailable; "
                                f"text-only fallback) ---\n{_truncate_for_safety(pp.page.text)}"
                            ),
                        }
                    )
                continue
            except Exception as e:
                # Per-page render failure shouldn't block the whole workflow.
                if pp.page.text:
                    parts.append(
                        {
                            "type": "text",
                            "text": (
                                f"--- {file_label} :: {idx_label} (render failed: "
                                f"{type(e).__name__}; text-only fallback) ---\n"
                                f"{_truncate_for_safety(pp.page.text)}"
                            ),
                        }
                    )
                continue

            parts.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{b64}",
                        "detail": detail,
                    },
                }
            )

    return parts


# ---------------------------------------------------------------------------
# Convenience wrapper used by the frontend
# ---------------------------------------------------------------------------


def build_content_parts_for_files(
    paths: list[str],
    budget_tokens: int = INPUT_TOKEN_BUDGET_PER_CALL,
) -> tuple[list[dict], int, list[ContentPlan]]:
    """
    End-to-end helper: analyze every PDF, plan, build content parts, and
    return the merged content list + total estimated tokens + per-file plans.

    Raises ``TooLargeError`` if even the degraded plan exceeds ``budget_tokens``
    summed across all files.
    """
    plans: list[ContentPlan] = []
    remaining = int(budget_tokens)
    for p in paths:
        pages = analyze_pdf(p)
        plan = plan_content_strategy(p, pages, budget_tokens=remaining)
        plans.append(plan)
        remaining = max(0, remaining - plan.estimated_tokens)
        if remaining <= 0 and len(plans) < len(paths):
            raise TooLargeError(
                f"Token budget exhausted after {len(plans)} of {len(paths)} files. "
                f"Reduce the number of uploaded files or split the largest one."
            )

    merged: list[dict] = []
    for plan in plans:
        merged.extend(build_content_parts(plan))
    total = sum(plan.estimated_tokens for plan in plans)
    return merged, total, plans

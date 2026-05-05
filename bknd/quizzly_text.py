"""
Lightweight text helpers shared across Quizzly.

Intentionally kept dependency-free (no Streamlit, no LangChain) so UI modules
can safely import these helpers without pulling in heavy backend dependencies.
"""

import re


def clean_option_text(s: str) -> str:
    """Strip up to 3 repeated option-letter prefixes.

    Many model outputs already include things like ``"A) ..."`` inside the option string,
    so when the UI also renders the letter we end up with ``"A) A) ..."``.
    Used by the error-notebook and current-quiz-mistakes views.
    """
    if not isinstance(s, str):
        return ""
    t = s.strip()
    # Examples we want to normalize:
    # - "A) foo", "A ) foo", "(A) foo", "[A] foo"
    # - "A. foo", "A: foo", "A - foo", "A—foo", "A）foo"
    # - repeated prefixes: "A) A) foo"
    prefix = re.compile(r"^\s*[\(\[\{]?\s*([ABCD])\s*[\)\]\}]?\s*[\)\.\:\-\u2013\u2014\uFF09]\s*",
                        re.IGNORECASE)
    for _ in range(3):
        m = prefix.match(t)
        if not m:
            break
        t = t[m.end() :].lstrip()
    return t


"""Shared helpers for ``fntnd.views`` modules.

Kept in a dedicated module (rather than ``fntnd/views/__init__.py``) so view
submodules don't have to import from their own package's init. On Python 3.14 +
Streamlit Cloud, package-level imports are the most fragile point during a
cold-start dotted-import race, and ``quizzly_main.py`` eager-loads this file
before any view consumer.
"""

# NOTE: deliberately no ``from __future__ import annotations`` — on Python 3.14
# (PEP 649) deferred annotations interact badly with some module-loading paths
# we exercise in ``quizzly_main.py``.


def clean_option_text(s: str) -> str:
    """Strip up to 3 repeated ``A) `` / ``B) `` / ``C) `` / ``D) `` prefixes.

    Many model outputs already include ``"A) ..."`` inside the option string,
    so when the UI also renders the letter we end up with ``"A) A) ..."``.
    Used by the error-notebook and current-quiz-mistakes views.
    """
    if not isinstance(s, str):
        return ""
    t = s.strip()
    for _ in range(3):
        if len(t) >= 3 and t[0].upper() in "ABCD" and t[1] == ")" and t[2] == " ":
            t = t[3:].lstrip()
        else:
            break
    return t

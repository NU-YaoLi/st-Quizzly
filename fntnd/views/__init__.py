"""View renderers for Quizzly frontend."""


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


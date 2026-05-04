"""
Streamlit entrypoint for Quizzly.

Loads ``quizzly_config``, the ``bknd`` package, and the ``fntnd`` package
explicitly via ``SourceFileLoader`` and registers them in ``sys.modules`` before
invoking ``fntnd.quizzly_ftnd.main()``. The custom loader exists to side-step
the Python 3.14 dotted-import ``KeyError`` (and its cascade of ``cannot import
name X`` errors) that we hit on Streamlit Cloud cold starts.
"""

import importlib
import importlib.util
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

# Repo root must win on sys.path (Streamlit Cloud cwd is not always the project folder).
_root = Path(__file__).resolve().parent
_root_str = str(_root)
if sys.path[0] != _root_str:
    try:
        sys.path.remove(_root_str)
    except ValueError:
        pass
    sys.path.insert(0, _root_str)


def _load_module(name: str, file_path: Path) -> None:
    """Load a ``.py`` file by path and register it in ``sys.modules`` as ``name``.

    Works for both top-level modules (e.g. ``quizzly_config``) and dotted
    submodules (e.g. ``bknd.quizzly_rate_limit``). Uses ``SourceFileLoader`` so
    execution matches the normal import path — module-level names are bound
    correctly. Avoids the Python 3.14 ``KeyError`` we hit on Streamlit Cloud
    when relying on dotted-import machinery.
    """
    path_str = str(file_path.resolve())
    if not file_path.is_file():
        raise ImportError(f"Required module missing: {path_str}")
    loader = SourceFileLoader(name, path_str)
    spec = importlib.util.spec_from_file_location(name, path_str, loader=loader)
    if spec is None:
        raise ImportError(f"Could not create spec for module {name} from {path_str}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        loader.exec_module(mod)
    except Exception:
        # Avoid leaving a half-initialized module that breaks subsequent imports.
        sys.modules.pop(name, None)
        raise


# Every public name downstream code imports from ``quizzly_config``. Kept in
# sync with the module by hand — it's small, slow-moving, and worth the
# explicit list because it's the only thing standing between us and the
# Python 3.14 / Streamlit Cloud cold-start cascade.
_REQUIRED_CONFIG_NAMES = (
    "MIN_QUESTIONS",
    "MAX_WEB_URL_SLOTS",
    "DAILY_GENERATION_LIMIT",
    "SUPABASE_URL",
    "QUIZZLY_MODEL",
    "WEB_CHARS_PER_PAGE",
    "WEB_TEXT_PER_URL_CAP",
    "MAX_QUESTIONS_CAP",
    "FILE_FINGERPRINT_BYTES",
    "WEB_FETCH_CACHE_TTL_SECS",
    "ANSWER_LETTERS",
    "MODEL_PRICING_USD_PER_1K",
)

# Snapshot of quizzly_config values, populated right after the first successful
# load. Used to "self-heal" sys.modules['quizzly_config'] if any name later goes
# missing (which is what the recurring "cannot import name X from quizzly_config"
# Cloud errors look like).
_CONFIG_SNAPSHOT: dict[str, object] = {}


def _verify_quizzly_config() -> None:
    """Make sure ``quizzly_config`` has every name downstream code imports.

    On Streamlit Cloud + Python 3.14 we've seen the module land in
    ``sys.modules`` with only some of its names bound, which then cascades into
    ``cannot import name 'X' from 'quizzly_config'`` errors several modules
    later. This function reloads via our own ``_load_module`` (which is what
    actually works under 3.14), and falls back to the normal import machinery
    only as a last resort. Any partial module is popped so it can't poison
    later imports.
    """
    mod = sys.modules.get("quizzly_config")
    if mod is None or any(not hasattr(mod, n) for n in _REQUIRED_CONFIG_NAMES):
        # Force a clean reload through our own SourceFileLoader path.
        sys.modules.pop("quizzly_config", None)
        importlib.invalidate_caches()
        _load_module("quizzly_config", _root / "quizzly_config.py")
        mod = sys.modules.get("quizzly_config")
    if mod is None:
        raise ImportError("quizzly_config was not registered in sys.modules.")
    missing = [n for n in _REQUIRED_CONFIG_NAMES if not hasattr(mod, n)]
    if missing:
        # Last-ditch fallback: try the normal import machinery.
        try:
            sys.modules.pop("quizzly_config", None)
            importlib.invalidate_caches()
            mod2 = importlib.import_module("quizzly_config")
            missing2 = [n for n in _REQUIRED_CONFIG_NAMES if not hasattr(mod2, n)]
            if not missing2:
                sys.modules["quizzly_config"] = mod2
                return
            # Don't leave a partially-bound module in sys.modules — it would
            # silently feed downstream "cannot import name" errors on retry.
            sys.modules.pop("quizzly_config", None)
            mod = mod2
            missing = missing2
        except Exception:
            sys.modules.pop("quizzly_config", None)
        raise ImportError(
            f"quizzly_config from {getattr(mod, '__file__', '?')} is missing names {missing}. "
            "If this is Streamlit Cloud, confirm quizzly_config.py is committed and not overwritten."
        )


def _snapshot_quizzly_config() -> None:
    """Cache every required ``quizzly_config`` value after the initial load."""
    mod = sys.modules["quizzly_config"]
    for n in _REQUIRED_CONFIG_NAMES:
        _CONFIG_SNAPSHOT[n] = getattr(mod, n)


def _reinforce_quizzly_config() -> None:
    """Re-bind any required names that vanished from ``sys.modules['quizzly_config']``.

    Cheap (just ``hasattr`` + ``setattr``) and idempotent. Called between
    eager loads so a downstream ``from quizzly_config import X`` can never hit
    a half-stripped module on Python 3.14 + Streamlit Cloud.
    """
    mod = sys.modules.get("quizzly_config")
    if mod is None or not _CONFIG_SNAPSHOT:
        # Nothing to reinforce yet — defer to the full verify path.
        _verify_quizzly_config()
        return
    for name, value in _CONFIG_SNAPSHOT.items():
        if not hasattr(mod, name):
            setattr(mod, name, value)


def _load_package(name: str, init_path: Path) -> None:
    """Load a package from its __init__.py and register submodule search path."""
    if not init_path.is_file():
        raise ImportError(f"Required package init missing: {init_path}")
    pkg_dir = str(init_path.parent)
    # Use SourceFileLoader for package init too (more stable than spec_from_file_location on 3.14 Cloud).
    path_str = str(init_path.resolve())
    loader = SourceFileLoader(name, path_str)
    spec = importlib.util.spec_from_file_location(
        name,
        path_str,
        loader=loader,
        submodule_search_locations=[pkg_dir],
    )
    if spec is None:
        raise ImportError(f"Could not load spec for package {name}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        loader.exec_module(mod)
    except Exception:
        # Match _load_module: avoid leaving a half-initialized package in sys.modules,
        # which would otherwise feed cascading "cannot import name X" errors on retry.
        sys.modules.pop(name, None)
        raise


_load_module("quizzly_config", _root / "quizzly_config.py")
_verify_quizzly_config()
_snapshot_quizzly_config()
_load_package("bknd", _root / "bknd" / "__init__.py")

import streamlit as st

st.set_page_config(page_title="Quizzly", page_icon="📖", layout="wide")

# Load backend modules by path (avoid Python 3.14 Cloud dotted-import KeyError).
# Order matters: dependents come after their dependencies. ``_reinforce_quizzly_config``
# is a cheap pre-check that re-binds any config name that may have been
# stripped from sys.modules between the previous step and this one — that's the
# defensive answer to the recurring "cannot import name X from quizzly_config".
_reinforce_quizzly_config()
_load_module("bknd.quizzly_usage_log", _root / "bknd" / "quizzly_usage_log.py")
_load_module("bknd.quizzly_user_ip", _root / "bknd" / "quizzly_user_ip.py")
_reinforce_quizzly_config()
_load_module("bknd.quizzly_question_upldprcs", _root / "bknd" / "quizzly_question_upldprcs.py")
_reinforce_quizzly_config()
_load_module("bknd.quizzly_rate_limit", _root / "bknd" / "quizzly_rate_limit.py")
# Shared text helpers used by lightweight views.
_load_module("bknd.quizzly_text", _root / "bknd" / "quizzly_text.py")
# Eager-load the rest of the bknd surface so the first request doesn't trip the
# Python 3.14 dotted-import KeyError on a cold worker.
_reinforce_quizzly_config()
_load_module("bknd.quizzly_question_gnrt", _root / "bknd" / "quizzly_question_gnrt.py")
_reinforce_quizzly_config()
_load_module("bknd.quizzly_question_vrf", _root / "bknd" / "quizzly_question_vrf.py")
_reinforce_quizzly_config()
_load_module("bknd.quizzly_analytics", _root / "bknd" / "quizzly_analytics.py")

_load_package("fntnd", _root / "fntnd" / "__init__.py")

# Eager-load fntnd helpers BEFORE the views and the main UI, so any
# ``from fntnd.quizzly_state import ...`` / ``from fntnd.quizzly_client_ip import ...``
# resolves through sys.modules instead of tripping the 3.14 dotted-import KeyError.
_reinforce_quizzly_config()
_load_module("fntnd.quizzly_state", _root / "fntnd" / "quizzly_state.py")
_load_module("fntnd.quizzly_client_ip", _root / "fntnd" / "quizzly_client_ip.py")

# Explicitly load view modules to avoid Python 3.14 KeyError during normal import resolution.
_load_package("fntnd.views", _root / "fntnd" / "views" / "__init__.py")
_reinforce_quizzly_config()
_load_module(
    "fntnd.views.quizzly_current_quiz_mistakes",
    _root / "fntnd" / "views" / "quizzly_current_quiz_mistakes.py",
)
_load_module(
    "fntnd.views.quizzly_data_analysis_view",
    _root / "fntnd" / "views" / "quizzly_data_analysis_view.py",
)
_load_module(
    "fntnd.views.quizzly_error_notebook_view",
    _root / "fntnd" / "views" / "quizzly_error_notebook_view.py",
)
_load_module(
    "fntnd.views.quizzly_howtouse_view",
    _root / "fntnd" / "views" / "quizzly_howtouse_view.py",
)

# Then load the main frontend module by path.
_reinforce_quizzly_config()
_load_module("fntnd.quizzly_ftnd", _root / "fntnd" / "quizzly_ftnd.py")
if not hasattr(sys.modules.get("fntnd.quizzly_ftnd"), "main"):
    raise ImportError("Failed to load fntnd.quizzly_ftnd.main (module did not finish initializing).")
main = sys.modules["fntnd.quizzly_ftnd"].main


if __name__ == "__main__":
    main()

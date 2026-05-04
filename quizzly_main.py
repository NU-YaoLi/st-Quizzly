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


def _verify_quizzly_config() -> None:
    mod = sys.modules.get("quizzly_config")
    if mod is None:
        # Streamlit Cloud + Python 3.14 can occasionally drop sys.modules entries mid-reload.
        # Reload from disk once rather than crashing.
        _load_module("quizzly_config", _root / "quizzly_config.py")
        mod = sys.modules.get("quizzly_config")
    if mod is None:
        raise ImportError("quizzly_config was not registered in sys.modules.")
    required = (
        "MIN_QUESTIONS",
        "DAILY_GENERATION_LIMIT",
        "ANSWER_LETTERS",
        "SUPABASE_URL",
        "QUIZZLY_MODEL",
        "MODEL_PRICING_USD_PER_1K",
    )
    missing = [n for n in required if not hasattr(mod, n)]
    if missing:
        # One more attempt: import via normal import machinery (sometimes more stable on Cloud reloads).
        try:
            sys.modules.pop("quizzly_config", None)
            importlib.invalidate_caches()
            mod2 = importlib.import_module("quizzly_config")
            missing2 = [n for n in required if not hasattr(mod2, n)]
            if not missing2:
                sys.modules["quizzly_config"] = mod2
                return
            mod = mod2
            missing = missing2
        except Exception:
            pass
        raise ImportError(
            f"quizzly_config from {getattr(mod, '__file__', '?')} is missing names {missing}. "
            "If this is Streamlit Cloud, confirm quizzly_config.py is committed and not overwritten."
        )


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
_load_package("bknd", _root / "bknd" / "__init__.py")

import streamlit as st

st.set_page_config(page_title="Quizzly", page_icon="📖", layout="wide")

# Load backend modules by path (avoid Python 3.14 Cloud dotted-import KeyError).
# Order matters: dependents come after their dependencies.
_load_module("bknd.quizzly_usage_log", _root / "bknd" / "quizzly_usage_log.py")
_load_module("bknd.quizzly_user_ip", _root / "bknd" / "quizzly_user_ip.py")
_load_module("bknd.quizzly_question_upldprcs", _root / "bknd" / "quizzly_question_upldprcs.py")
_load_module("bknd.quizzly_rate_limit", _root / "bknd" / "quizzly_rate_limit.py")
# Eager-load the rest of the bknd surface so the first request doesn't trip the
# Python 3.14 dotted-import KeyError on a cold worker.
_load_module("bknd.quizzly_question_gnrt", _root / "bknd" / "quizzly_question_gnrt.py")
_load_module("bknd.quizzly_question_vrf", _root / "bknd" / "quizzly_question_vrf.py")
_load_module("bknd.quizzly_analytics", _root / "bknd" / "quizzly_analytics.py")

_load_package("fntnd", _root / "fntnd" / "__init__.py")

# Explicitly load view modules to avoid Python 3.14 KeyError during normal import resolution.
_load_package("fntnd.views", _root / "fntnd" / "views" / "__init__.py")
# Helpers must load before the view submodules that depend on them. Keeping the
# helper in its own module (instead of fntnd/views/__init__.py) prevents view
# files from depending on their own package's init, which is the most common
# place the cold-start race shows up.
_load_module(
    "fntnd.views._helpers",
    _root / "fntnd" / "views" / "_helpers.py",
)
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
_load_module("fntnd.quizzly_ftnd", _root / "fntnd" / "quizzly_ftnd.py")
if not hasattr(sys.modules.get("fntnd.quizzly_ftnd"), "main"):
    raise ImportError("Failed to load fntnd.quizzly_ftnd.main (module did not finish initializing).")
main = sys.modules["fntnd.quizzly_ftnd"].main


if __name__ == "__main__":
    main()

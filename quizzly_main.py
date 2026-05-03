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


def _load_top_level_module(name: str, file_path: Path) -> None:
    """Load a single-file module by path (avoids Python 3.14 import KeyError on Streamlit Cloud).

    Uses ``SourceFileLoader`` instead of ``spec_from_file_location`` so execution matches the
    normal import path and all module-level names (e.g. ``DAILY_GENERATION_LIMIT``) are bound.
    """
    path_str = str(file_path.resolve())
    if not file_path.is_file():
        raise ImportError(f"Required file missing: {path_str}")
    loader = SourceFileLoader(name, path_str)
    spec = importlib.util.spec_from_loader(name, loader, origin=path_str)
    if spec is None:
        raise ImportError(f"Could not create spec for {name} from {path_str}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)


def _verify_quizzly_config() -> None:
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
        raise ImportError(
            f"quizzly_config from {getattr(mod, '__file__', '?')} is missing names {missing}. "
            "If this is Streamlit Cloud, confirm quizzly_config.py is committed and not overwritten."
        )


def _load_package(name: str, init_path: Path) -> None:
    """Load a package from its __init__.py and register submodule search path."""
    if not init_path.is_file():
        raise ImportError(f"Required package init missing: {init_path}")
    pkg_dir = str(init_path.parent)
    spec = importlib.util.spec_from_file_location(
        name,
        init_path,
        submodule_search_locations=[pkg_dir],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load spec for package {name}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)


def _load_module(name: str, file_path: Path) -> None:
    """Load a .py file as module ``name`` (supports dotted names)."""
    path_str = str(file_path.resolve())
    if not file_path.is_file():
        raise ImportError(f"Required module missing: {path_str}")
    loader = SourceFileLoader(name, path_str)
    spec = importlib.util.spec_from_loader(name, loader, origin=path_str)
    if spec is None:
        raise ImportError(f"Could not create spec for module {name} from {path_str}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)


_load_top_level_module("quizzly_config", _root / "quizzly_config.py")
_verify_quizzly_config()
_load_package("bknd", _root / "bknd" / "__init__.py")

import streamlit as st

st.set_page_config(page_title="Quizzly", page_icon="📖", layout="wide")

# Submodules import ``streamlit`` and ``quizzly_config`` — load after those exist.
for _mod in (
    "bknd.quizzly_usage_log",
    "bknd.quizzly_user_ip",
    "bknd.quizzly_question_upldprcs",
    "bknd.quizzly_rate_limit",
):
    importlib.import_module(_mod)

_load_package("fntnd", _root / "fntnd" / "__init__.py")

# Explicitly load view modules to avoid Python 3.14 KeyError during normal import resolution.
_load_package("fntnd.views", _root / "fntnd" / "views" / "__init__.py")
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
main = sys.modules["fntnd.quizzly_ftnd"].main


if __name__ == "__main__":
    main()

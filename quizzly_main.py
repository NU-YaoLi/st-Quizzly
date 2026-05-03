import importlib
import importlib.util
import sys
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
    """Load a single-file module by path (avoids Python 3.14 import KeyError on Streamlit Cloud)."""
    if not file_path.is_file():
        raise ImportError(f"Required file missing: {file_path}")
    spec = importlib.util.spec_from_file_location(name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load spec for {name} from {file_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)


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


_load_top_level_module("quizzly_config", _root / "quizzly_config.py")
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

main = importlib.import_module("fntnd.quizzly_ftnd").main


if __name__ == "__main__":
    main()

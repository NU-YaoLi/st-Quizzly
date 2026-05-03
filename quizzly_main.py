import sys
from pathlib import Path

# Ensure repo root is on sys.path (Streamlit Cloud cwd can differ from the entrypoint folder).
_root = Path(__file__).resolve().parent
_root_str = str(_root)
if _root_str not in sys.path:
    sys.path.insert(0, _root_str)

# Fail fast with ImportError if this file is missing (Cloud cwd issues are handled via sys.path above).
import quizzly_config  # noqa: F401

import streamlit as st

st.set_page_config(page_title="Quizzly", page_icon="📖", layout="wide")

from fntnd.quizzly_ftnd import main


if __name__ == "__main__":
    main()


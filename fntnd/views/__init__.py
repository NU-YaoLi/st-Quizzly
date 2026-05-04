"""View renderers for Quizzly frontend.

Intentionally kept empty: shared helpers live in ``fntnd.views._helpers`` so
view submodules don't have to import from their own package's ``__init__``,
which is the cold-start race point on Python 3.14 + Streamlit Cloud.
"""

import streamlit as st

st.set_page_config(page_title="Quizzly", page_icon="📖", layout="wide")

from fntnd.quizzly_ftnd import main


if __name__ == "__main__":
    main()


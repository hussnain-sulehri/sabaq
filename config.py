"""
Key loading for Sabaq.

Order of lookup:
1. Streamlit secrets  - used when deployed on Streamlit Cloud
2. Environment / .env - used when running on your own machine

Keys are never written to disk, never printed, and never shown in the UI.
"""

import os

import streamlit as st
from dotenv import load_dotenv

load_dotenv()


def get_key(name: str) -> str | None:
    """Return an API key by name, or None if it is not set anywhere."""
    try:
        value = st.secrets[name]
        if value:
            return str(value).strip()
    except Exception:
        pass

    value = os.environ.get(name)
    return value.strip() if value else None


def mask(key: str) -> str:
    """Show only the last 4 characters, for confirming the right key is loaded."""
    if not key or len(key) < 8:
        return "****"
    return "*" * (len(key) - 4) + key[-4:]

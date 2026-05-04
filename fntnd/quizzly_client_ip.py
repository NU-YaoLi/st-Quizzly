"""
Browser-based public-IP hydration for Streamlit apps.

Streamlit Community Cloud (and many other hosts) hide the visitor's real IP
behind private hops, so headers-only detection returns RFC1918 addresses or
nothing useful. We let the browser fetch its own public IP from a small set of
public endpoints and cache the result in ``st.session_state``.

Side effects (in ``st.session_state``):
- ``_quizzly_public_ip_js``: str — the resolved public IP (IPv4 preferred).
- ``_quizzly_public_ip_js_err``: str — short error string when the JS could not
  reach any endpoint, or when the Python-side call to ``st_javascript`` failed.

Design notes:
- ``streamlit-javascript`` evaluates the script via ``await eval(js_code)``, so
  a top-level ``await`` is a SyntaxError. We wrap the work in an async IIFE and
  return a Promise that ``await`` can unwrap.
- We do NOT block the script run waiting for the IP to resolve. The component
  triggers a Streamlit auto-rerun via ``setComponentValue`` when ready; until
  then ``get_client_ip()`` returns ``"unknown"``. Action handlers that need a
  real IP (e.g. before a long DB write) should check and rerun once — see the
  pre-generation guard in ``quizzly_ftnd.main()``.
"""

from __future__ import annotations

import streamlit as st


_COMPONENT_KEY = "quizzly_public_ip_v4_async_iife"

# Module-level constant: built once at import time, not on every Streamlit rerun.
_IP_FETCH_SCRIPT = """
(async function () {
  function isIPv4(s) {
    return /^(\\d{1,3}\\.){3}\\d{1,3}$/.test(String(s || "").trim());
  }
  function ipFromJson(j) {
    if (!j || typeof j !== "object") return "";
    const v = j.ip ?? j.IP ?? j.query;
    return v ? String(v).trim() : "";
  }
  const v4Json = ["https://api.ipify.org?format=json"];
  const v4Text = ["https://checkip.amazonaws.com", "https://ipv4.icanhazip.com"];
  const fallback = [
    "https://api64.ipify.org?format=json",
    "https://ifconfig.co/json",
    "https://ipwho.is/?output=json",
  ];
  for (const url of v4Json) {
    try {
      const r = await fetch(url, { cache: "no-store" });
      if (!r.ok) continue;
      const ip = ipFromJson(await r.json());
      if (ip && isIPv4(ip)) return { ip };
    } catch (e) {}
  }
  for (const url of v4Text) {
    try {
      const r = await fetch(url, { cache: "no-store" });
      if (!r.ok) continue;
      const ip = (await r.text()).trim();
      if (ip && isIPv4(ip)) return { ip };
    } catch (e) {}
  }
  for (const url of fallback) {
    try {
      const r = await fetch(url, { cache: "no-store" });
      if (!r.ok) continue;
      const ip = ipFromJson(await r.json());
      if (ip) return { ip };
    } catch (e) {}
  }
  return { error: "all IP endpoints failed" };
})()
""".strip()


def hydrate_public_ip() -> None:
    """Mount the IP-fetching browser component (no-op once an IP is cached)."""
    if st.session_state.get("_quizzly_public_ip_js"):
        return
    try:
        from streamlit_javascript import st_javascript  # type: ignore
    except Exception as e:
        st.session_state["_quizzly_public_ip_js_err"] = (
            f"import: {type(e).__name__}: {e!s}"
        )[:500]
        return

    try:
        # Older PyPI builds of streamlit-javascript only accept ``(js_code, key)``;
        # do NOT pass ``default=`` here or the call raises TypeError on those builds.
        res = st_javascript(_IP_FETCH_SCRIPT, key=_COMPONENT_KEY)
    except Exception as e:
        st.session_state["_quizzly_public_ip_js_err"] = (
            f"hydrate: {type(e).__name__}: {e!s}"
        )[:500]
        return

    if isinstance(res, dict) and res.get("ip"):
        new_ip = str(res["ip"]).strip()
        prev_ip = st.session_state.get("_quizzly_public_ip_js")
        st.session_state["_quizzly_public_ip_js"] = new_ip
        st.session_state.pop("_quizzly_public_ip_js_err", None)
        if prev_ip != new_ip:
            # IP changed → invalidate the cached `user_ip` row id so the next DB
            # write resolves a fresh row.
            st.session_state.pop("_quizzly_user_ip_id", None)
    elif isinstance(res, dict) and res.get("error"):
        st.session_state["_quizzly_public_ip_js_err"] = str(res["error"])[:500]
    elif isinstance(res, str) and res.strip():
        st.session_state["_quizzly_public_ip_js_err"] = res.strip()[:500]
    # else: res is 0/None — JS hasn't phoned home yet; Streamlit will auto-rerun.


def render_ip_debug_caption() -> None:
    """Single-line debug caption combining current IP cache and last error."""
    ip = st.session_state.get("_quizzly_public_ip_js") or "(not set)"
    err = st.session_state.get("_quizzly_public_ip_js_err")
    parts = [f"Client IP debug — js_cache={ip}"]
    if err:
        parts.append(f"err={err}")
    st.caption(" | ".join(parts))

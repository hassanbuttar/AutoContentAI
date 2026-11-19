"""Use the operating system's certificate store for outbound HTTPS (httpx, anthropic, etc.).

Machines behind TLS-inspecting proxies trust the proxy's root CA in the OS store, which certifi's bundle lacks
(symptom: CERTIFICATE_VERIFY_FAILED for some hosts). Disable with AUTOCONTENT_SYSTEM_TRUST=0."""
from __future__ import annotations

import logging
import os

log = logging.getLogger("autocontent")
_done = False


def use_system_trust() -> None:
    global _done
    if _done or os.environ.get("AUTOCONTENT_SYSTEM_TRUST", "1") == "0":
        return
    try:
        import truststore

        truststore.inject_into_ssl()
        _done = True
    except Exception as exc:  # noqa: BLE001 - optional; certifi remains the fallback
        log.warning("System certificate store not used (%s); falling back to certifi", exc)

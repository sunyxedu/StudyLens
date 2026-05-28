"""Login locally with a real browser, then push the session state to the Railway backend."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

from studylens.tools.browser_state import save_browser_state


def main() -> None:
    backend_url = os.environ.get("STUDYLENS_BACKEND_URL")
    admin_token = os.environ.get("STUDYLENS_ADMIN_TOKEN")
    if not backend_url or not admin_token:
        sys.exit("Set STUDYLENS_BACKEND_URL and STUDYLENS_ADMIN_TOKEN in your environment.")

    local_path = Path("data/auth/browser-state.json")
    asyncio.run(save_browser_state(local_path))

    payload = json.loads(local_path.read_text(encoding="utf-8"))
    response = httpx.post(
        f"{backend_url.rstrip('/')}/admin/browser-state",
        headers={"X-Admin-Token": admin_token},
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    print(f"Pushed browser state to {backend_url}: {response.json()}")


if __name__ == "__main__":
    main()

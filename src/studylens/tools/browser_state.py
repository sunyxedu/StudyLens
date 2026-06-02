from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

from studylens.api.browser_state import DEFAULT_BROWSER_STATE_STEPS


async def save_browser_state(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/136.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        for index, step in enumerate(DEFAULT_BROWSER_STATE_STEPS, start=1):
            await page.goto(step.url, wait_until="domcontentloaded")
            print(f"\n[{index}/{len(DEFAULT_BROWSER_STATE_STEPS)}] {step.title}")
            print(step.instruction)
            print("When this step is loaded, press Enter here.")
            input()

        await context.storage_state(path=str(output))
        await browser.close()
        print(f"\nSaved browser storage state to {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Save authenticated Playwright browser state.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/auth/browser-state.json"),
        help="Path to write Playwright storage state JSON.",
    )
    args = parser.parse_args()
    asyncio.run(save_browser_state(args.output))


def push_user_browser_state() -> None:
    """Capture course logins locally, then upload them to your hosted account.

    Reads the StudyLens URL/credentials from env (STUDYLENS_BACKEND_URL,
    STUDYLENS_USERNAME, STUDYLENS_PASSWORD) or prompts for them, opens a real
    browser to log into each course site, then POSTs the captured state to
    /browser-state/upload authenticated as your user.
    """
    import getpass
    import json
    import os

    import httpx

    backend = os.environ.get("STUDYLENS_BACKEND_URL") or input(
        "StudyLens URL (e.g. https://studylens-production.up.railway.app): "
    ).strip()
    username = os.environ.get("STUDYLENS_USERNAME") or input("StudyLens username: ").strip()
    password = os.environ.get("STUDYLENS_PASSWORD") or getpass.getpass("StudyLens password: ")
    if not backend or not username or not password:
        raise SystemExit("StudyLens URL, username, and password are all required.")

    output = Path("data/auth/browser-state.json")
    asyncio.run(save_browser_state(output))
    state = json.loads(output.read_text(encoding="utf-8"))

    with httpx.Client(base_url=backend.rstrip("/"), timeout=30) as client:
        login = client.post("/auth/login", json={"username": username, "password": password})
        if login.status_code != 200:
            raise SystemExit(f"Login failed ({login.status_code}): {login.text}")
        # The session cookie set by /auth/login is carried by the client jar.
        upload = client.post("/browser-state/upload", json=state)
        if upload.status_code != 200:
            raise SystemExit(f"Upload failed ({upload.status_code}): {upload.text}")
        print(f"Uploaded browser state to {backend}: {upload.json()}")


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

from studylens.api.browser_state import DEFAULT_BROWSER_STATE_STEPS

DEFAULT_BACKEND_URL = "https://studylens-production.up.railway.app"


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
    """Sign into your StudyLens account, then capture and upload course logins.

    Prompts for your StudyLens username and password (or reads them from
    STUDYLENS_USERNAME / STUDYLENS_PASSWORD) and signs in first. Only once the
    credentials check out does it open a real browser to log into each course
    site, then POST the captured state to /browser-state/upload as your user.

    The hosted backend is used by default; override it with STUDYLENS_BACKEND_URL.
    """
    import getpass
    import json
    import os
    import sys

    import httpx

    def _prompt(label: str) -> str:
        # Write+flush the prompt to stdout ourselves, then read, so the prompt
        # always shows before we block. getpass writes its prompt to /dev/tty,
        # which can race ahead of a buffered stdout prompt (e.g. under `uv run`)
        # and print the username and password prompts together on one line.
        sys.stdout.write(label)
        sys.stdout.flush()
        return sys.stdin.readline().strip()

    backend = (os.environ.get("STUDYLENS_BACKEND_URL") or DEFAULT_BACKEND_URL).rstrip("/")

    username = os.environ.get("STUDYLENS_USERNAME") or _prompt("StudyLens username: ")
    password = os.environ.get("STUDYLENS_PASSWORD")
    if not password:
        sys.stdout.write("StudyLens password: ")
        sys.stdout.flush()
        password = getpass.getpass("")  # empty prompt: we already printed it above
    if not username or not password:
        raise SystemExit("StudyLens username and password are both required.")

    with httpx.Client(base_url=backend, timeout=30) as client:
        # Verify the credentials before bothering to open a browser.
        login = client.post("/auth/login", json={"username": username, "password": password})
        if login.status_code != 200:
            raise SystemExit(f"Login failed ({login.status_code}): {login.text}")
        print(f"Signed in to {backend} as {username}. Opening a browser to capture course logins…")

        output = Path("data/auth/browser-state.json")
        asyncio.run(save_browser_state(output))
        state = json.loads(output.read_text(encoding="utf-8"))

        # The session cookie set by /auth/login is carried by the client jar.
        upload = client.post("/browser-state/upload", json=state)
        if upload.status_code != 200:
            raise SystemExit(f"Upload failed ({upload.status_code}): {upload.text}")
        print(f"Uploaded browser state to {backend}: {upload.json()}")


if __name__ == "__main__":
    main()

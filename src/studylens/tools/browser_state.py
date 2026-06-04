from __future__ import annotations

import argparse
import asyncio
import base64
import getpass
from collections.abc import Callable
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from studylens.api.browser_state import DEFAULT_BROWSER_STATE_STEPS

DEFAULT_BACKEND_URL = "http://127.0.0.1:8000/"
CredentialsProvider = Callable[[], dict[str, str] | None]


async def save_browser_state(
    output: Path,
    *,
    credentials_provider: CredentialsProvider | None = None,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
        except PlaywrightError as exc:
            message = str(exc).lower()
            if "executable doesn't exist" in message or "playwright install" in message:
                raise SystemExit(
                    "Playwright's browser isn't installed yet. Run:\n"
                    "    uv run playwright install chromium\n"
                    "then re-run this command."
                ) from exc
            raise
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/136.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        for index, step in enumerate(DEFAULT_BROWSER_STATE_STEPS, start=1):
            if step.key == "exams" and credentials_provider is not None:
                await _apply_basic_auth_header(page, credentials_provider())
            await page.goto(step.url, wait_until="domcontentloaded")
            print(f"\n[{index}/{len(DEFAULT_BROWSER_STATE_STEPS)}] {step.title}")
            print(step.instruction)
            print("When this step is loaded, press Enter here.")
            input()
            if step.key == "exams":
                await page.set_extra_http_headers({})

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
    asyncio.run(
        save_browser_state(
            args.output,
            credentials_provider=_prompt_imperial_credentials,
        )
    )


def push_user_browser_state() -> None:
    """Sign into your StudyLens account, then capture and upload course logins.

    Prompts for your StudyLens username and password (or reads them from
    STUDYLENS_USERNAME / STUDYLENS_PASSWORD) and signs in first. Only once the
    credentials check out does it open a real browser to log into each course
    site, then POST the captured state to /browser-state/upload as your user.

    The hosted backend is used by default; override it with STUDYLENS_BACKEND_URL.
    """
    import json
    import os

    import httpx

    backend = (os.environ.get("STUDYLENS_BACKEND_URL") or DEFAULT_BACKEND_URL).rstrip("/")

    username = os.environ.get("STUDYLENS_USERNAME") or _prompt_text("StudyLens username: ")
    password = os.environ.get("STUDYLENS_PASSWORD")
    if not password:
        password = _prompt_password("StudyLens password: ")
    if not username or not password:
        raise SystemExit("StudyLens username and password are both required.")

    with httpx.Client(base_url=backend, timeout=30) as client:
        # Verify the credentials before bothering to open a browser.
        login = client.post("/auth/login", json={"username": username, "password": password})
        if login.status_code != 200:
            raise SystemExit(f"Login failed ({login.status_code}): {login.text}")
        print(f"Signed in to {backend} as {username}. Opening a browser to capture course logins…")

        output = Path("data/auth/browser-state.json")
        asyncio.run(
            save_browser_state(
                output,
                credentials_provider=_prompt_imperial_credentials,
            )
        )
        state = json.loads(output.read_text(encoding="utf-8"))

        # The session cookie set by /auth/login is carried by the client jar.
        upload = client.post("/browser-state/upload", json=state)
        if upload.status_code != 200:
            raise SystemExit(f"Upload failed ({upload.status_code}): {upload.text}")
        print("This stage is finished.")


def _prompt_imperial_credentials() -> dict[str, str] | None:
    print("\nDOC Exams uses Imperial HTTP Basic authentication.")
    username = _prompt_text("Imperial username: ")
    password = _prompt_password("Imperial password: ")
    if not username or not password:
        return None
    return {"username": username, "password": password}


def _prompt_text(label: str) -> str:
    try:
        with open("/dev/tty", "r+", encoding="utf-8") as tty:
            tty.write(label)
            tty.flush()
            return tty.readline().strip()
    except OSError:
        return input(label).strip()


def _prompt_password(label: str) -> str:
    return getpass.getpass(label)


async def _apply_basic_auth_header(
    page: object,
    credentials: dict[str, str] | None,
) -> None:
    if not credentials:
        return
    token = base64.b64encode(
        f"{credentials['username']}:{credentials['password']}".encode()
    ).decode("ascii")
    await page.set_extra_http_headers({"Authorization": f"Basic {token}"})


if __name__ == "__main__":
    main()

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


if __name__ == "__main__":
    main()

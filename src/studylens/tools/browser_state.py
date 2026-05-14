from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

PANOPTO_URL = (
    "https://imperial.cloud.panopto.eu/Panopto/Pages/Sessions/List.aspx#isSharedWithMe=true"
)
EDSTEM_URL = "https://edstem.org/us/dashboard"


async def save_browser_state(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto(PANOPTO_URL, wait_until="domcontentloaded")
        print("\nLog into Imperial/Panopto in the opened browser.")
        print("After Panopto is logged in, press Enter here.")
        input()

        await page.goto(EDSTEM_URL, wait_until="domcontentloaded")
        print("\nLog into EdStem in the opened browser.")
        print("After EdStem is logged in, press Enter here to save browser state.")
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

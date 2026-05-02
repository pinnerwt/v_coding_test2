"""Drive the SPA with a real browser, screenshot, then dump the trace."""

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

GOAL = "Go to CanIRun.ai and recommend me the best model that I can host locally"
URL = "http://127.0.0.1:8001/"
OUT = Path(__file__).parent.parent / "data" / "smoke"
OUT.mkdir(parents=True, exist_ok=True)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()
        page.on("console", lambda m: print(f"[console:{m.type}] {m.text}", file=sys.stderr))
        page.on("pageerror", lambda e: print(f"[pageerror] {e}", file=sys.stderr))

        await page.goto(URL)
        await page.wait_for_selector("#goal")

        await page.screenshot(path=str(OUT / "01_initial.png"), full_page=True)

        await page.fill("#goal", GOAL)
        await page.click("#run")

        # Snapshot after kickoff
        await asyncio.sleep(2)
        await page.screenshot(path=str(OUT / "02_kicked_off.png"), full_page=True)

        # Wait for either a few step cards or up to 90s
        deadline = asyncio.get_event_loop().time() + 90
        last_count = 0
        while asyncio.get_event_loop().time() < deadline:
            cards = await page.locator("#transcript .card").count()
            done = await page.locator("#transcript .pill.success, #transcript .pill.failed").count()
            if done > 0:
                print(f"[smoke] terminal done card seen, cards={cards}")
                break
            if cards != last_count:
                print(f"[smoke] cards={cards}")
                last_count = cards
            await asyncio.sleep(1)

        await page.screenshot(path=str(OUT / "03_mid_or_end.png"), full_page=True)

        # Try expanding the first step card to verify collapse/expand
        try:
            await page.locator("#transcript .card").nth(1).click()
            await asyncio.sleep(0.3)
            await page.screenshot(path=str(OUT / "04_expanded.png"), full_page=True)
        except Exception as e:
            print(f"[smoke] expand skipped: {e}")

        # Read URL bar to confirm pushState
        href = page.url
        print(f"[smoke] final URL = {href}")

        await browser.close()


asyncio.run(main())

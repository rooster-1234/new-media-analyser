import asyncio
from playwright.async_api import async_playwright
import re

async def test_engine(browser_type_name):
    print(f"Testing {browser_type_name}...")
    async with async_playwright() as p:
        engine = getattr(p, browser_type_name)
        try:
            browser = await engine.launch(headless=True)
            context = await browser.new_context(
                locale="en-US"
            )
            page = await context.new_page()
            url = "https://www.imdb.com/title/tt27041231/"
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            print(f"{browser_type_name} status:", resp.status)
            title = await page.title()
            print(f"{browser_type_name} title:", title)
            content = await page.content()
            m = re.search(r'"ratingValue":\s*"?([0-9.]+)"?', content)
            if m:
                print(f"{browser_type_name} ratingValue:", m.group(1))
            m2 = re.search(r'([0-9]\.[0-9])/10', content)
            if m2:
                print(f"{browser_type_name} regex rating:", m2.group(1))
            await browser.close()
        except Exception as e:
            print(f"{browser_type_name} error:", e)

async def main():
    for name in ["firefox", "webkit", "chromium"]:
        await test_engine(name)

asyncio.run(main())

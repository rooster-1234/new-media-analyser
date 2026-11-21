"""
Test script to probe and analyze new media search sites (The Pirate Bay, uIndex, ext.to).
Tests access via Playwright with stealth evasions and identifies page structure,
Cloudflare protections, and torrent link extraction.
"""

import asyncio
import os
import re
from typing import Dict, Any
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

load_dotenv()

SITES = {
    "Site 1 (The Pirate Bay)": os.getenv("SITE_SCAN1") or os.getenv("SITE_SCAN") or "https://thepiratebay.org/search.php?q=top100:48h_207",
    "Site 2 (uIndex)": os.getenv("SITE_SCAN2") or "https://uindex.org/top.php?c=1&t=7d",
    "Site 3 (ext.to)": os.getenv("SITE_SCAN3") or "https://ext.to/browse/?cat=1&sort=age&order=desc&age=4",
}


async def test_site(p, name: str, url: str) -> Dict[str, Any]:
    print(f"\nTesting: {name}")
    print(f"URL: {url}")
    print("-" * 65)

    result = {
        "name": name,
        "url": url,
        "accessible": False,
        "cloudflare_blocked": False,
        "status_code": None,
        "page_title": "",
        "items_found": 0,
        "magnets_found": 0,
        "notes": "",
    }

    browser = await p.chromium.launch(headless=True)
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
    )
    page = await context.new_page()
    await Stealth().apply_stealth_async(page)

    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        await page.wait_for_timeout(3000)

        result["status_code"] = response.status if response else None
        result["page_title"] = (await page.title()).strip()
        html_content = await page.content()

        # Check for Cloudflare / anti-bot challenge
        cf_indicators = [
            "Just a moment...",
            "Checking your browser",
            "Attention Required! | Cloudflare",
            "cf-turnstile",
            "challenges.cloudflare.com",
            "Please wait while your request is being verified",
        ]
        if any(ind in html_content or ind in result["page_title"] for ind in cf_indicators):
            result["cloudflare_blocked"] = True
            result["notes"] = "Cloudflare anti-bot challenge detected (Turnstile / Ray ID gate)."
        elif response and response.status in (403, 503):
            result["cloudflare_blocked"] = True
            result["notes"] = f"HTTP {response.status} Access Denied / Cloudflare WAF."
        else:
            result["accessible"] = True

        # Extract torrent links & magnets
        magnets = await page.locator("a[href^='magnet:']").all()
        result["magnets_found"] = len(magnets)

        # Site-specific selectors
        if "piratebay" in url:
            entries = await page.locator("#torrents li.list-entry").all()
            result["items_found"] = len(entries)
            result["notes"] = f"Successfully parsed {len(entries)} releases and {len(magnets)} direct magnet links."

        elif "uindex" in url:
            rows = await page.locator("table tr").all()
            result["items_found"] = max(0, len(rows) - 1)
            sample_info = ""
            if len(rows) > 1:
                first_row = rows[1]
                cols = await first_row.locator("td").all()
                if len(cols) >= 3:
                    t_title = await cols[1].inner_text()
                    t_size = await cols[3].inner_text() if len(cols) > 3 else "N/A"
                    sample_info = f" Sample row: '{t_title.strip()[:40]}' | Size: {t_size.strip()}"
            result["notes"] = f"Accessible (Status {result['status_code']}). Parsed {result['items_found']} rows and {len(magnets)} direct magnet links.{sample_info}"

        elif "ext.to" in url:
            links = await page.locator("a[href*='/torrent/'], tr, .item").all()
            result["items_found"] = len(links)
            if not result["cloudflare_blocked"]:
                result["notes"] = f"Accessible! Found {len(links)} release elements and {len(magnets)} magnets."

    except Exception as e:
        result["notes"] = f"Exception during navigation: {str(e)}"
    finally:
        await browser.close()

    print(f"Title     : {result['page_title']}")
    print(f"HTTP Code : {result['status_code']}")
    print(f"Cloudflare: {'BLOCKED (Requires FlareSolverr/Bypass)' if result['cloudflare_blocked'] else 'Not Triggered'}")
    print(f"Items     : {result['items_found']}")
    print(f"Magnets   : {result['magnets_found']}")
    print(f"Notes     : {result['notes']}")

    return result


async def main():
    print("=" * 65)
    print("        Testing Playwright Scanner on Search Sites")
    print("=" * 65)

    async with async_playwright() as p:
        for name, url in SITES.items():
            await test_site(p, name, url)

    print("\n" + "=" * 65)


if __name__ == "__main__":
    asyncio.run(main())

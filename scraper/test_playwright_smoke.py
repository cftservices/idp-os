"""
Playwright smoke test — verifies browser launch and basic navigation.
Does NOT require LinkedIn login.
Run: python test_playwright_smoke.py
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

CHROMIUM_PROFILE = os.getenv("CHROMIUM_PROFILE", "c:/tools/linkedin-intel/browser-profile")


def run_smoke_test():
    from playwright.sync_api import sync_playwright

    print("=" * 60)
    print("LinkedIn Intel — Playwright Smoke Test")
    print("=" * 60)

    with sync_playwright() as p:
        print(f"\n[1/4] Launching Chromium with persistent profile...")
        print(f"      Profile path: {CHROMIUM_PROFILE}")

        context = p.chromium.launch_persistent_context(
            user_data_dir=CHROMIUM_PROFILE,
            headless=False,
            args=["--start-maximized"],
            no_viewport=True,
        )
        print("      [OK] Browser launched")

        page = context.new_page()

        print("\n[2/4] Navigating to linkedin.com...")
        page.goto("https://www.linkedin.com", wait_until="domcontentloaded", timeout=30000)
        title = page.title()
        print(f"      [OK] Page title: {title}")

        print("\n[3/4] Checking login status...")
        # If logged in, feed link is visible. If not, login form is visible.
        is_logged_in = page.query_selector("a[href*='/feed/']") is not None
        # LinkedIn shows login differently depending on locale/layout — check broadly
        has_login_form = page.query_selector(
            "input#username, input[name='session_key'], "
            "a[href*='/login'], a[href*='/uas/login'], button[data-tracking-control-name*='sign_in']"
        ) is not None

        if is_logged_in:
            print("      [OK] Already logged in! Feed link detected.")
            print("\n[4/4] Quick feed test -- navigating to feed...")
            page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
            import time
            time.sleep(2)
            # Count visible posts
            posts = page.query_selector_all("div.feed-shared-update-v2, div[data-urn*='activity']")
            print(f"      [OK] Feed loaded. Detected {len(posts)} post elements.")
            if len(posts) == 0:
                print("      [WARN] No posts detected -- CSS selectors may need updating for current LinkedIn layout")
        elif has_login_form:
            print("      [WARN] Not logged in. Login / sign-in page detected.")
            print("      -> Run the one-time login script from SETUP.md first")
            print("\n[4/4] Keeping browser open for 10 seconds so you can see the login page...")
            import time
            time.sleep(10)
        else:
            # Fallback: if we're on linkedin.com but neither selector matched, treat as logged-out
            url = page.url
            print(f"      [INFO] Page loaded. URL: {url}")
            print("      [WARN] Could not detect login state from DOM selectors.")
            print("      -> Check: is the user logged in? If not, follow SETUP.md to log in first.")
            print("\n[4/4] Keeping browser open for 5 seconds...")
            import time
            time.sleep(5)

        context.close()
        print("\n" + "=" * 60)
        print("Smoke test complete.")
        if is_logged_in:
            print("[OK] Ready to run: python run.py")
        else:
            print("[WARN] Next step: log in using SETUP.md instructions, then run: python run.py")
        print("=" * 60)


if __name__ == "__main__":
    run_smoke_test()

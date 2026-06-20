"""Standalone login runner.

Opens a visible browser, logs into Instagram with the credentials in
config.json, and saves the session cookies to ig_state.json so all future
runs (including the GUI) can scrape headlessly without logging in again.

Usage:
    python login.py            # visible browser (recommended, handles 2FA)
    python login.py --headless # no window (fails if 2FA is required)
"""
import asyncio
import sys

# Force UTF-8 stdout so status glyphs print on Windows (cp1252) consoles.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.config import Config
from app.scraper.instagram_scraper import InstagramScraper


async def main():
    headless = "--headless" in sys.argv
    cfg = Config.load()

    print(f"Conta: @{cfg.ig_username}")
    print(f"Salvando sessão em: {cfg.state_path}")
    print("Abrindo navegador… complete o 2FA na janela, se solicitado.\n")

    scraper = InstagramScraper(cfg.ig_username, cfg.ig_password, cfg.state_path)
    ok, msg = await scraper.login(
        headless=headless,
        progress_cb=lambda m: print("  ", m),
    )

    print()
    if ok:
        print(f"✔ {msg}")
        print(f"✔ Cookies salvos em {cfg.state_path} — prontos para uso headless.")
    else:
        print(f"✘ {msg}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

"""Ad-hoc end-to-end scraping test for a single account.

Usage: python test_scrape.py <username> [max_items]
Scrapes posts with the saved session, then runs the LLM analysis pipeline
and writes a CSV. Keeps volume small to respect rate limits.
"""
import asyncio
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.config import Config, AccountConfig
from app.scraper.instagram_scraper import InstagramScraper
from app.llm.analyzer import LLMAnalyzer
from app.utils import csv_exporter


async def main():
    username = sys.argv[1] if len(sys.argv) > 1 else "iburaordinario_res"
    max_items = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    cfg = Config.load()
    scraper = InstagramScraper(cfg.ig_username, cfg.ig_password, cfg.state_path)

    account = AccountConfig(
        username=username,
        scrape_posts=True,
        scrape_stories=False,  # keep the smoke test focused on posts
        scrape_reels=False,
    )

    print(f"=== Scraping @{username} (max {max_items} posts) ===\n")
    items = []

    def on_item(item):
        items.append(item)

    collected = await scraper.scrape_all(
        accounts=[account],
        max_items=max_items,
        progress_cb=lambda m: print(m),
        result_cb=on_item,
    )

    print(f"\n=== Collected {len(collected)} items ===")
    for i, it in enumerate(collected, 1):
        img = f"{len(it.image_bytes)}B" if it.image_bytes else "no-img"
        text = (it.text or "").replace("\n", " ")[:90]
        print(f"[{i}] {it.content_type} | author=@{it.author} | source=@{it.source_account} "
              f"| loc={it.location or '-'} | img={img}")
        print(f"    text: {text}")

    if not collected:
        print("\nNo items collected — see log above.")
        return

    # --- LLM analysis pipeline ---
    print(f"\n=== Running LLM analysis ({cfg.text_model} / {cfg.vision_model}) ===")
    analyzer = LLMAnalyzer(cfg.ollama_endpoint, cfg.text_model, cfg.vision_model)
    results = []
    for it in collected:
        res = analyzer.analyze(it, cfg.keywords)
        if res:
            results.append(res)
            print(f"  author=@{res.author} | source=@{res.source_account} | prob={res.probability} "
                  f"| city={res.city or '-'} | {res.reason[:60]}")

    if results:
        out = f"test_{username}.csv"
        n = csv_exporter.export(results, out)
        print(f"\n=== Wrote {n} rows -> {out} ===")


if __name__ == "__main__":
    asyncio.run(main())

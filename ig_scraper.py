"""
Instagram epidemiological scraper — Carnaval / Recife.

Pipeline (all local, no cloud LLM):

    Playwright (logged-in Chromium)  ->  raw HTML / story media
            |
            v
    ScrapeGraphAI + Ollama (qwen2.5)  ->  structured JSON (username/text/location)
            |
            v
    Ollama risk classifier            ->  public-health risk label per item

Capabilities
------------
* login_and_save_state ....... save a logged-in browser session once
* collect_links_for_hashtags . harvest post/reel links from hashtags
* collect_links_for_pages .... harvest recent feed-post links from select pages
* scrape_posts ............... posts -> HTML -> extracted CSV
* scrape_stories_for_pages ... STORIES from a list of select pages -> CSV + media
* classify_risk_csv .......... add public-health risk labels to a CSV

Everything is async-friendly and works from a Jupyter notebook (see the .ipynb)
or as a plain script:  `python ig_scraper.py --help`
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

import config

IG = "https://www.instagram.com"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# ===========================================================================
# 1. Authentication — save a logged-in session once
# ===========================================================================
async def login_and_save_state(state_path: str = config.STATE_PATH, wait_seconds: int = 120):
    """Open a real browser, let the human log in, then persist cookies/localStorage.

    Run this ONCE. The saved ``state_path`` is reused by every other function so
    you never automate the login form (which trips Instagram's defenses).
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(user_agent=DEFAULT_UA, locale="pt-BR")
        page = await context.new_page()
        await page.goto(IG, wait_until="domcontentloaded")
        print(f"Faça login manualmente. Você tem {wait_seconds}s, depois a sessão será salva...")
        await page.wait_for_timeout(wait_seconds * 1000)
        await context.storage_state(path=state_path)
        await browser.close()
        print(f"Sessão salva em {state_path}")


_DISMISS_LABELS = ["Not now", "Not Now", "Agora não", "Save info", "Salvar informações",
                   "Allow all cookies", "Permitir todos os cookies", "Aceitar", "Allow"]


async def login_with_credentials(
    username: str,
    password: str,
    state_path: str = config.STATE_PATH,
    headless: bool = False,
    settle_seconds: int = 90,
) -> bool:
    """Log in with username/password and save the session **token** (storage_state).

    This is what the GUI's "Create Token" button calls. ``state_path`` (ig_state.json)
    is the reusable auth token every scrape function loads. The browser is shown by
    default (``headless=False``) so you can solve 2FA / "suspicious login" challenges
    by hand; once the feed loads, the session is saved. Returns True if login looked
    successful.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(user_agent=DEFAULT_UA, locale="pt-BR")
        page = await context.new_page()
        await page.goto(f"{IG}/accounts/login/", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        async def _dismiss_dialogs():
            for label in _DISMISS_LABELS:
                try:
                    btn = page.get_by_role("button", name=re.compile(f"^{re.escape(label)}$", re.I))
                    if await btn.count() > 0:
                        await btn.first.click()
                        await page.wait_for_timeout(500)
                except Exception:  # noqa: BLE001
                    pass

        await _dismiss_dialogs()
        try:
            await page.fill("input[name='username']", username, timeout=20000)
            await page.fill("input[name='password']", password)
            await page.click("button[type='submit']")
            print("Submitted login form...")
        except PWTimeout:
            print("Login form not found — finish logging in manually in the open window.")

        # Poll until we leave the login/challenge pages (or the user solves 2FA by hand).
        ok = False
        deadline = time.time() + settle_seconds
        while time.time() < deadline:
            await page.wait_for_timeout(2000)
            await _dismiss_dialogs()
            url = page.url
            if all(x not in url for x in ("/accounts/login", "/challenge", "two_factor", "/auth_platform")):
                ok = True
                await page.wait_for_timeout(2500)  # let cookies settle
                break

        await context.storage_state(path=state_path)
        await browser.close()
        status = "saved" if ok else "saved (login may be incomplete — check the browser)"
        print(f"Session token {status} -> {state_path}")
        return ok


def _new_context_kwargs(state_path: str) -> dict:
    return dict(
        storage_state=state_path,
        viewport={"width": 1600, "height": 900},
        user_agent=DEFAULT_UA,
        locale="pt-BR",
    )


# ===========================================================================
# 2. Link collection
# ===========================================================================
async def collect_post_links_logged_in(
    hashtag: str,
    state_path: str = config.STATE_PATH,
    max_links: int = 400,
    scrolls: int = 50,
) -> list[str]:
    """Collect post/reel permalinks from a hashtag explore page."""
    url = f"{IG}/explore/tags/{hashtag.lstrip('#').strip()}/"
    links: set[str] = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(**_new_context_kwargs(state_path))
        page = await context.new_page()
        page.set_default_navigation_timeout(240000)
        page.set_default_timeout(240000)
        await page.goto(url, wait_until="domcontentloaded")

        try:
            await page.wait_for_selector("a[href*='/p/'], a[href*='/reel/']", timeout=15000)
        except PWTimeout:
            await page.screenshot(path=f"debug_tag_{hashtag}.png", full_page=True)
            await browser.close()
            raise RuntimeError(
                f"No post links found for #{hashtag}. Saved debug_tag_{hashtag}.png. "
                "Likely a login wall/challenge or changed markup."
            )

        for _ in range(scrolls):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(4000)
            for a in await page.query_selector_all("a[href*='/p/'], a[href*='/reel/']"):
                href = await a.get_attribute("href") or ""
                if href.startswith("/p/") or href.startswith("/reel/"):
                    links.add(IG + href.split("?")[0])
            if len(links) >= max_links:
                break

        await browser.close()
    return list(links)[:max_links]


async def collect_links_for_hashtags(
    hashtags: Iterable[str],
    state_path: str = config.STATE_PATH,
    max_links_per_hashtag: int = 400,
    scrolls: int = 50,
    dedupe_global: bool = True,
) -> tuple[dict, list[str]]:
    """Run collect_post_links_logged_in over several hashtags."""
    results: dict[str, list[str]] = {}
    all_links: set[str] = set()

    for tag in hashtags:
        tag = tag.lstrip("#").strip()
        print(f"\n=== Collecting #{tag} ===")
        links = await collect_post_links_logged_in(
            tag, state_path=state_path, max_links=max_links_per_hashtag, scrolls=scrolls
        )
        if dedupe_global:
            new = [u for u in links if u not in all_links]
            all_links.update(new)
            results[tag] = new
            print(f"#{tag}: {len(new)} new links (of {len(links)} collected)")
        else:
            results[tag] = links
            print(f"#{tag}: {len(links)} links collected")

    post_links = list(all_links) if dedupe_global else [u for t in results for u in results[t]]
    return results, post_links


async def collect_links_for_pages(
    pages: Iterable[str],
    state_path: str = config.STATE_PATH,
    max_links_per_page: int = 60,
    scrolls: int = 12,
) -> list[str]:
    """Collect recent FEED-POST permalinks from a list of select account pages."""
    all_links: list[str] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(**_new_context_kwargs(state_path))
        for user in pages:
            user = user.lstrip("@").strip()
            page = await context.new_page()
            page.set_default_timeout(120000)
            seen: set[str] = set()
            try:
                await page.goto(f"{IG}/{user}/", wait_until="domcontentloaded")
                await page.wait_for_selector("a[href*='/p/'], a[href*='/reel/']", timeout=15000)
                for _ in range(scrolls):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(2500)
                    for a in await page.query_selector_all("a[href*='/p/'], a[href*='/reel/']"):
                        href = await a.get_attribute("href") or ""
                        if href.startswith("/p/") or href.startswith("/reel/"):
                            seen.add(IG + href.split("?")[0])
                    if len(seen) >= max_links_per_page:
                        break
                print(f"@{user}: {len(seen)} post links")
                all_links.extend(list(seen)[:max_links_per_page])
            except PWTimeout:
                print(f"@{user}: no posts found (private/empty/challenge)")
            finally:
                await page.close()
        await browser.close()
    # dedupe, preserve order
    return list(dict.fromkeys(all_links))


# ===========================================================================
# 3. HTML fetch + local-LLM extraction
# ===========================================================================
async def fetch_html_logged_in(url: str, state_path: str = config.STATE_PATH, settle_ms: int = 2000) -> str:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(**_new_context_kwargs(state_path))
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(settle_ms)
        html = await page.content()
        await browser.close()
        return html


EXTRACTION_PROMPT = """
Extract the Instagram post data from the provided HTML.

Return ONLY valid JSON with these keys:
- username: the author's handle (without @)
- text: the post caption text (empty string if missing)
- location: location title (NA if missing)

JSON only. No commentary.
"""


def extract_from_html_with_sga(html: str, prompt: str = EXTRACTION_PROMPT) -> dict:
    """Run ScrapeGraphAI's SmartScraperGraph over HTML using the local Ollama model."""
    # Imported lazily so link-collection works even without scrapegraphai installed.
    from scrapegraphai.graphs import SmartScraperGraph

    graph = SmartScraperGraph(prompt=prompt, source=html, config=config.scrapegraph_config())
    data = graph.run() or {}
    return data.get("content", data) if isinstance(data, dict) else {}


async def scrape_posts(
    post_links: list[str],
    state_path: str = config.STATE_PATH,
    out_csv: str = config.POSTS_CSV,
    max_n: int | None = None,
    concurrency: int = 2,
    nav_timeout_ms: int = 60000,
    settle_ms: int = 2500,
) -> pd.DataFrame:
    """Posts -> logged-in HTML -> local-LLM extraction -> CSV."""
    urls = post_links[:max_n] if max_n else list(post_links)
    rows: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(**_new_context_kwargs(state_path))

        async def route_handler(route):
            if route.request.resource_type in ("image", "media", "font"):
                await route.abort()
            else:
                await route.continue_()

        await context.route("**/*", route_handler)
        sem = asyncio.Semaphore(concurrency)

        async def process_one(url: str) -> dict:
            async with sem:
                page = await context.new_page()
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout_ms)
                    await page.wait_for_timeout(settle_ms)
                    html = await page.content()
                    # ScrapeGraphAI is sync; keep the event loop responsive.
                    content = await asyncio.to_thread(extract_from_html_with_sga, html)
                    return {
                        "url": url,
                        "username": (content.get("username") or "").strip(),
                        "text": (content.get("text") or "").strip(),
                        "location": (content.get("location") or "").strip(),
                        "error": "",
                    }
                except Exception as e:  # noqa: BLE001 — record and continue
                    return {"url": url, "username": "", "text": "", "location": "", "error": str(e)}
                finally:
                    await page.close()

        tasks = [process_one(u) for u in urls]
        for idx, coro in enumerate(asyncio.as_completed(tasks), start=1):
            row = await coro
            rows.append(row)
            print(f"[{idx}/{len(urls)}] {'FAIL: ' + row['error'][:120] if row['error'] else 'OK'}")

        await browser.close()

    df = pd.DataFrame(rows)
    df.to_csv(out_csv, index=False, encoding="utf-8")
    print(f"Saved {len(df)} rows -> {out_csv}")
    return df


# ===========================================================================
# 4. STORIES from a list of select pages   (the new capability)
# ===========================================================================
# Instagram stories live at /stories/<username>/ and play in a special viewer.
# They are ephemeral image/video segments with optional overlay text, mentions,
# location and hashtag stickers. We open the viewer logged-in, step through every
# segment, and for each one capture: timestamp, media URL, any DOM/overlay text,
# and a full screenshot (so the text-on-image can be OCR'd / LLM-read later).
#
# The story DOM is heavily obfuscated and changes often, so selectors are kept
# defensive and we degrade gracefully instead of crashing the whole run.

_VIEW_STORY_LABELS = ["View story", "Ver story", "Ver stories", "Visualizar story"]


async def _open_story_viewer(page) -> bool:
    """Click the 'View story' splash button if Instagram shows one. Returns True if a viewer is open."""
    for label in _VIEW_STORY_LABELS:
        try:
            btn = page.get_by_role("button", name=re.compile(label, re.I))
            if await btn.count() > 0:
                await btn.first.click()
                await page.wait_for_timeout(1200)
                return True
        except Exception:  # noqa: BLE001
            pass
    # No splash button — viewer may already be open (story segments present).
    return await page.query_selector("section [role='button'], section video, section img") is not None


async def _capture_story_segment(page, username: str, idx: int, media_dir: Path) -> dict:
    """Pull what we can out of the currently displayed story segment."""
    media_url = ""
    # Prefer video, then the high-res story image.
    video = await page.query_selector("section video")
    if video:
        media_url = await video.get_attribute("src") or ""
    if not media_url:
        for sel in ("section img[srcset]", "img[src*='cdninstagram']", "section img"):
            img = await page.query_selector(sel)
            if img:
                srcset = await img.get_attribute("srcset")
                media_url = (srcset.split(",")[-1].strip().split(" ")[0] if srcset else
                             await img.get_attribute("src")) or ""
                if media_url:
                    break

    # Timestamp — the viewer renders a <time datetime="..."> element.
    timestamp = ""
    t = await page.query_selector("section time, time")
    if t:
        timestamp = await t.get_attribute("datetime") or (await t.inner_text() or "")

    # Any text that lives in the DOM (mentions, captions, link stickers, poll text).
    overlay_text = ""
    try:
        container = await page.query_selector("section")
        if container:
            raw = await container.inner_text()
            # Drop obvious UI chrome lines; keep human-looking text.
            lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
            noise = {"Mais", "More", "Seguir", "Follow", "Curtir", "Like", username}
            overlay_text = " | ".join(ln for ln in lines if ln not in noise and len(ln) > 1)[:1000]
    except Exception:  # noqa: BLE001
        pass

    # Screenshot the rendered segment (image text often only exists as pixels).
    shot_path = media_dir / f"{username}_{idx:02d}.png"
    try:
        await page.screenshot(path=str(shot_path), full_page=False)
    except Exception:  # noqa: BLE001
        shot_path = Path("")

    return {
        "username": username,
        "segment": idx,
        "timestamp": timestamp,
        "media_url": media_url,
        "overlay_text": overlay_text,
        "screenshot": str(shot_path),
        "error": "",
    }


async def scrape_stories_for_pages(
    pages: Iterable[str] = None,
    state_path: str = config.STATE_PATH,
    out_csv: str = config.STORIES_CSV,
    media_dir: str = config.STORIES_MEDIA_DIR,
    max_segments_per_user: int = 40,
    settle_ms: int = 1500,
) -> pd.DataFrame:
    """Scrape current stories from each account in ``pages``.

    For every story segment we record timestamp, media URL, DOM/overlay text and
    a screenshot. Returns a DataFrame and writes ``out_csv``. Screenshots land in
    ``media_dir`` so the picture-text can be OCR'd or fed to the LLM afterwards.
    """
    pages = list(pages if pages is not None else config.STORY_PAGES)
    media_path = Path(media_dir)
    media_path.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(**_new_context_kwargs(state_path))

        for user in pages:
            user = user.lstrip("@").strip()
            page = await context.new_page()
            page.set_default_timeout(60000)
            try:
                await page.goto(f"{IG}/stories/{user}/", wait_until="domcontentloaded")
                await page.wait_for_timeout(settle_ms)

                if not await _open_story_viewer(page):
                    print(f"@{user}: no active story (or not visible to this account)")
                    rows.append({"username": user, "segment": -1, "timestamp": "", "media_url": "",
                                 "overlay_text": "", "screenshot": "", "error": "no_active_story"})
                    continue

                seen_media: set[str] = set()
                for idx in range(max_segments_per_user):
                    await page.wait_for_timeout(settle_ms)

                    # Leaving /stories/<user>/ means we've run past this user's reel.
                    if f"/stories/{user}/" not in page.url:
                        break

                    seg = await _capture_story_segment(page, user, idx, media_path)
                    # A repeated media URL means the reel looped / didn't advance — stop.
                    if seg["media_url"] and seg["media_url"] in seen_media:
                        break
                    if seg["media_url"]:
                        seen_media.add(seg["media_url"])
                    rows.append(seg)

                    # Advance to the next segment. ArrowRight is the most reliable control.
                    await page.keyboard.press("ArrowRight")

                print(f"@{user}: captured {len([r for r in rows if r['username'] == user and r['segment'] >= 0])} segment(s)")
            except PWTimeout:
                print(f"@{user}: timeout opening stories")
                rows.append({"username": user, "segment": -1, "timestamp": "", "media_url": "",
                             "overlay_text": "", "screenshot": "", "error": "timeout"})
            except Exception as e:  # noqa: BLE001
                print(f"@{user}: error {e}")
                rows.append({"username": user, "segment": -1, "timestamp": "", "media_url": "",
                             "overlay_text": "", "screenshot": "", "error": str(e)})
            finally:
                await page.close()

        await browser.close()

    df = pd.DataFrame(rows)
    df.to_csv(out_csv, index=False, encoding="utf-8")
    print(f"Saved {len(df)} story rows -> {out_csv}  (screenshots in {media_dir}/)")
    return df


# ===========================================================================
# 5. Public-health risk classification (local Ollama chat)
# ===========================================================================
RISK_SYSTEM = """You are a careful public-health triage classifier.
Classify whether the text suggests an epidemiological danger/crisis/event (e.g., outbreak,
many people ill, suspected contamination, mass vomiting/diarrhea, unusual cluster,
hospital overload, etc.). Do NOT diagnose individuals. Use only the text evidence.
Output in Portuguese.
Return ONLY JSON: {"risk":"low|medium|high","signal":"short reason"}"""


def ollama_chat(model: str, system: str, user: str, timeout: int = 120) -> dict:
    """One-shot JSON chat against the local Ollama server."""
    r = requests.post(
        f"{config.OLLAMA_BASE_URL}/api/chat",
        json={
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.0},
        },
        timeout=timeout,
    )
    r.raise_for_status()
    return json.loads(r.json()["message"]["content"])


def classify_risk_df(
    df: pd.DataFrame,
    text_col: str = "text",
    loc_col: str = "location",
    model: str | None = None,
    throttle_s: float = 0.2,
) -> pd.DataFrame:
    """Add 'risk' and 'risk_signal' columns to a DataFrame (returns a new frame)."""
    model = model or config.CLASSIFIER_MODEL
    df = df.copy()
    if loc_col not in df.columns:
        df[loc_col] = ""

    risks, signals = [], []
    total = len(df)
    for i, (_, row) in enumerate(df.iterrows(), start=1):
        text = str(row.get(text_col, "") or "").strip()[:2000]
        loc = str(row.get(loc_col, "") or "")
        try:
            out = ollama_chat(model, RISK_SYSTEM, f"LOCATION: {loc}\nTEXT: {text}\n\nReturn JSON.")
            risks.append(out.get("risk", "low"))
            signals.append(out.get("signal", ""))
        except Exception as e:  # noqa: BLE001
            risks.append("")
            signals.append(f"error: {e}")
        print(f"[classify {i}/{total}] {risks[-1] or 'ERR'}")
        time.sleep(throttle_s)

    df["risk"] = risks
    df["risk_signal"] = signals
    return df


def classify_risk_csv(
    in_path: str = config.POSTS_CSV,
    out_path: str = config.RISK_CSV,
    text_col: str = "text",
    loc_col: str = "location",
    model: str | None = None,
    throttle_s: float = 0.2,
) -> pd.DataFrame:
    """CSV wrapper around classify_risk_df."""
    df = classify_risk_df(pd.read_csv(in_path), text_col, loc_col, model, throttle_s)
    df.to_csv(out_path, index=False, encoding="utf-8")
    print(f"Saved risk-classified CSV -> {out_path}")
    return df


# ===========================================================================
# 6. Excel export + one-call orchestration (used by the GUI)
# ===========================================================================
def export_to_excel(sheets: dict[str, pd.DataFrame], out_path: str = config.OUTPUT_XLSX) -> str:
    """Write one or more DataFrames to an .xlsx workbook (one sheet each)."""
    real = {k: v for k, v in sheets.items() if v is not None}
    if not real:
        raise ValueError("Nothing to export — every sheet is empty.")
    with pd.ExcelWriter(out_path, engine="openpyxl") as xw:
        for name, df in real.items():
            (df if not df.empty else pd.DataFrame({"info": ["no rows"]})).to_excel(
                xw, sheet_name=str(name)[:31], index=False
            )
    print(f"Excel written -> {out_path}  ({len(real)} sheet(s))")
    return out_path


async def run_pipeline(
    source: str,
    *,
    hashtags: list[str] | None = None,
    feed_pages: list[str] | None = None,
    story_pages: list[str] | None = None,
    max_links: int = 200,
    concurrency: int = 2,
    classify: bool = True,
    state_path: str = config.STATE_PATH,
    excel_path: str = config.OUTPUT_XLSX,
) -> dict[str, pd.DataFrame]:
    """Run a full scrape+classify+export, returning the sheets it wrote.

    ``source`` is one of: "hashtags", "pages", "stories".
    """
    sheets: dict[str, pd.DataFrame] = {}

    if source == "hashtags":
        _, links = await collect_links_for_hashtags(hashtags or config.HASHTAGS,
                                                    max_links_per_hashtag=max_links)
        sheets["posts"] = await scrape_posts(links, state_path=state_path, concurrency=concurrency)
    elif source == "pages":
        links = await collect_links_for_pages(feed_pages or config.FEED_PAGES,
                                              max_links_per_page=max_links)
        sheets["posts"] = await scrape_posts(links, state_path=state_path, concurrency=concurrency)
    elif source == "stories":
        sheets["stories"] = await scrape_stories_for_pages(story_pages or config.STORY_PAGES,
                                                           state_path=state_path)
    else:
        raise ValueError(f"unknown source: {source!r}")

    if classify:
        if "posts" in sheets and not sheets["posts"].empty:
            sheets["posts"] = classify_risk_df(sheets["posts"], "text", "location")
        if "stories" in sheets and not sheets["stories"].empty:
            sheets["stories"] = classify_risk_df(sheets["stories"], "overlay_text", "username")

    export_to_excel(sheets, excel_path)
    return sheets


# ===========================================================================
# CLI
# ===========================================================================
def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Carnaval/Recife Instagram epidemiological scraper (local LLM).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login", help="Open a browser to log in and save the session.")
    p_tags = sub.add_parser("hashtags", help="Collect post links from config.HASHTAGS, then scrape + classify.")
    p_tags.add_argument("--max", type=int, default=200)
    sub.add_parser("pages", help="Scrape recent feed posts from config.FEED_PAGES, then classify.")
    sub.add_parser("stories", help="Scrape current stories from config.STORY_PAGES.")
    p_risk = sub.add_parser("classify", help="Run risk classification on an existing CSV.")
    p_risk.add_argument("--in", dest="in_path", default=config.POSTS_CSV)
    return ap


async def _amain(args) -> None:
    if args.cmd == "login":
        await login_and_save_state()
    elif args.cmd == "hashtags":
        _, links = await collect_links_for_hashtags(config.HASHTAGS, max_links_per_hashtag=args.max)
        await scrape_posts(links, out_csv=config.POSTS_CSV)
        classify_risk_csv(config.POSTS_CSV, config.RISK_CSV)
    elif args.cmd == "pages":
        links = await collect_links_for_pages(config.FEED_PAGES)
        await scrape_posts(links, out_csv=config.POSTS_CSV)
        classify_risk_csv(config.POSTS_CSV, config.RISK_CSV)
    elif args.cmd == "stories":
        await scrape_stories_for_pages(config.STORY_PAGES)
    elif args.cmd == "classify":
        classify_risk_csv(args.in_path, config.RISK_CSV)


if __name__ == "__main__":
    args = _build_parser().parse_args()
    asyncio.run(_amain(args))

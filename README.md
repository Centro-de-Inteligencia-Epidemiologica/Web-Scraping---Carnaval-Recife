# Web Scraping — Carnaval Recife (Epidemiological Intelligence)

Scrapes Instagram **posts** (from hashtags or select pages) and **stories** (from a
list of select pages), extracts `username / text / location` with a **local LLM**,
and classifies each item for **public-health risk** (outbreak / mass-illness signals).

The whole pipeline runs **on your own machine via [Ollama](https://ollama.com)** —
no cloud LLM, no API keys, no per-token cost.

```
Playwright (logged-in Chromium)  ->  HTML / story media
        -> ScrapeGraphAI + Ollama (qwen2.5)  ->  structured JSON
        -> Ollama risk classifier            ->  risk label per item -> CSV
```

## Files
| File | Purpose |
|------|---------|
| `gui_app.py` | **Desktop GUI** (PySide6): enter credentials, create a token, export Excel. |
| `ig_scraper.py` | All scraping + extraction + classification logic (also a CLI). |
| `config.py` | Single place to set models, hashtags, pages, output paths. |
| `PythonscrapingInstagramV7.ipynb` | Notebook driver (cleaned, model-fixed, stories added). |
| `PythonscrapingInstagramV7.original.ipynb` | The original notebook, untouched, for reference. |
| `requirements.txt` | Python dependencies. |

---

## Which local model? (20 GB RTX 4000 Ada, Windows)

This workload is **structured JSON extraction + a short Portuguese risk label** — it
rewards instruction-following and clean JSON far more than raw size, so a mid-size
model is the sweet spot. The RTX 4000 Ada's **20 GB** leaves comfortable room for the
model *plus* the Chromium browser and the context window.

| Role | Recommended | VRAM (Q4_K_M) | Why |
|------|-------------|---------------|-----|
| **Primary** | `qwen2.5:14b` | ~9 GB | Best balance: strong multilingual (PT-BR) + very reliable strict JSON. Leaves ~10 GB headroom. |
| **Cheap/fast** | `qwen2.5:7b` | ~5 GB | Use for large batches when throughput matters more than accuracy. |
| **Alternative** | `gemma3:12b` | ~8 GB | Good Portuguese; solid second opinion. |
| **Max quality** | `gemma3:27b` | ~17 GB | Fits 20 GB but tight + slower; only if accuracy is critical. |
| **Embeddings (required)** | `nomic-embed-text` | ~0.3 GB | ScrapeGraphAI chunks/retrieves HTML with embeddings; keeps retrieval local. |

**Default = `qwen2.5:14b`** for both extraction and classification. To switch models,
change one line in `config.py` (or set the `EXTRACTION_MODEL` / `CLASSIFIER_MODEL`
environment variables) — every cell and the CLI follow automatically.

> Why not the original `ministral-3:3b` / `functiongemma`? Those tags aren't in the
> Ollama registry, so the pipeline couldn't actually pull or run them — that's the
> main reason "local LLM" didn't work out of the box. They've been replaced with real,
> installable models and the missing embeddings model has been added.

---

## Setup (Windows / PowerShell)

```powershell
# 1. Python deps
pip install -r requirements.txt
python -m playwright install chromium

# 2. Install Ollama (https://ollama.com/download) then pull the models
ollama pull qwen2.5:14b
ollama pull nomic-embed-text
# optional, faster batches:
# ollama pull qwen2.5:7b

# 3. Make sure Ollama is serving (the tray app does this; or run `ollama serve`)
```

Verify the GPU is actually being used: while a scrape runs, `ollama ps` should show
the model on `100% GPU`, and `nvidia-smi` should show ~9 GB used by `ollama`.

---

## Usage

### Option A — Desktop GUI (recommended)
```powershell
python gui_app.py
```
Three steps, mapped to the three tabs:

1. **Setup & Token** — type your Instagram **username + password**, pick the Ollama
   model, then click **Create Token**. A browser opens; finish any 2FA/challenge by
   hand. This saves `ig_state.json` — the reusable **session token** every scrape
   loads (the password itself is never written to disk).
2. **Scrape & Export** — choose *hashtags*, *select pages (feed)*, or *stories from
   select pages*; fill the inputs; tick "Classify public-health risk"; pick the
   **Excel output** file; click **Run**.
3. **Results** — preview the table, then open the `.xlsx` (one sheet per dataset:
   `posts` and/or `stories`, with `risk` / `risk_signal` columns when classification
   is on).

The scrape runs on a background thread, so the window stays responsive and streams a
live log at the bottom. Settings (username, models, lists, paths) persist between runs.

### Option B — Notebook
Open `PythonscrapingInstagramV7.ipynb` and run cells top to bottom:
**login once → collect links → scrape posts → scrape stories → classify risk.**

### Option B — CLI
```powershell
python ig_scraper.py login        # opens a browser; log in by hand (saves ig_state.json)
python ig_scraper.py hashtags     # hashtags -> posts -> extract -> classify
python ig_scraper.py pages        # select pages' feed posts -> extract -> classify
python ig_scraper.py stories      # current stories from config.STORY_PAGES
python ig_scraper.py classify --in carnaval_posts.csv
```

### Configure what gets scraped
Edit `config.py`:
- `HASHTAGS` — hashtags to harvest post links from.
- `FEED_PAGES` — accounts whose recent **posts** to scrape.
- `STORY_PAGES` — accounts whose **stories** to scrape (the "select pages").

---

## Stories scraping — what you get

For each account in `STORY_PAGES`, the logged-in story viewer is opened and **every
segment** is stepped through (`ArrowRight`). Per segment, `carnaval_stories.csv` records:

| column | meaning |
|--------|---------|
| `username` | the page the story belongs to |
| `segment` | 0-based segment index (`-1` = no active story) |
| `timestamp` | story publish time (`<time datetime>`) when available |
| `media_url` | direct image/video CDN URL of the segment |
| `overlay_text` | text found in the DOM (captions, mentions, link/poll stickers) |
| `screenshot` | path under `stories_media/` — for OCR / LLM reading of on-image text |

Notes & limits:
- Stories are **ephemeral** — only stories that are live *and visible to your logged-in
  account* are captured. Run regularly to build history.
- Most story content is **pixels on an image/video**; `overlay_text` only catches text
  that exists in the DOM. The screenshots are saved so you can OCR or vision-LLM them
  later if you need the burned-in text.
- The story DOM is obfuscated and changes often — selectors are defensive and the run
  degrades gracefully (records an error row) instead of crashing.

---

## Responsible use
This is a public-health intelligence tool. Use a dedicated research account, respect
Instagram's terms and rate limits (keep `concurrency` low and the throttle in place),
and handle any personal data in line with LGPD. The risk classifier flags *text-level
signals* for human review — it does **not** diagnose individuals.

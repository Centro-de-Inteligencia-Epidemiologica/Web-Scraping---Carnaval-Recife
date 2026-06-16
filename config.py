"""
Central configuration for the Carnaval/Recife Instagram epidemiological scraper.

Everything is driven by Ollama running LOCALLY (no cloud LLM, no API keys).
Tune the model choices below for your GPU. See README.md for the rationale
behind the defaults on a 20 GB RTX 4000 Ada.
"""

import os

# ---------------------------------------------------------------------------
# Ollama connection
# ---------------------------------------------------------------------------
# Ollama exposes an OpenAI-ish REST API on this address once `ollama serve`
# (or the Windows tray app) is running.
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

# ---------------------------------------------------------------------------
# Local models  (pull these once with `ollama pull <name>`)
# ---------------------------------------------------------------------------
# Naming note: ScrapeGraphAI wants the provider-prefixed form ("ollama/<tag>"),
# while the raw Ollama REST API (used by the risk classifier) wants the bare
# tag ("<tag>"). We keep ONE source of truth and derive both.
#
# Recommended for a 20 GB RTX 4000 Ada:
#   - qwen2.5:14b   -> best balance of Portuguese quality + strict JSON (~9 GB)
#   - qwen2.5:7b    -> faster/cheaper for large batches            (~5 GB)
#   - gemma3:12b    -> strong alternative, good Portuguese          (~8 GB)
# Switch by changing this single line:
EXTRACTION_MODEL = os.environ.get("EXTRACTION_MODEL", "qwen2.5:14b")

# Model used for the public-health risk classification step.
# Can be the same as EXTRACTION_MODEL or a smaller/faster one.
CLASSIFIER_MODEL = os.environ.get("CLASSIFIER_MODEL", "qwen2.5:14b")

# Embeddings model — ScrapeGraphAI's SmartScraperGraph chunks the HTML and
# retrieves the relevant pieces with embeddings. Without a local embedder it
# tries to reach OpenAI and fails. nomic-embed-text is tiny (~0.3 GB).
EMBEDDINGS_MODEL = os.environ.get("EMBEDDINGS_MODEL", "nomic-embed-text")

# Context window handed to the extraction LLM. Instagram post HTML is large;
# 8192 is a safe default on 20 GB. Lower to 4096 if you hit OOM.
MODEL_TOKENS = int(os.environ.get("MODEL_TOKENS", "8192"))


def scrapegraph_llm_config() -> dict:
    """LLM block for ScrapeGraphAI graph configs, wired to local Ollama."""
    return {
        "model": f"ollama/{EXTRACTION_MODEL}",
        "temperature": 0.0,
        "format": "json",
        "model_tokens": MODEL_TOKENS,
        "base_url": OLLAMA_BASE_URL,
    }


def scrapegraph_embeddings_config() -> dict:
    """Embeddings block so retrieval stays 100% local."""
    return {
        "model": f"ollama/{EMBEDDINGS_MODEL}",
        "base_url": OLLAMA_BASE_URL,
    }


def scrapegraph_config() -> dict:
    """Full graph_config consumed by SmartScraperGraph."""
    return {
        "llm": scrapegraph_llm_config(),
        "embeddings": scrapegraph_embeddings_config(),
        "verbose": False,
        "headless": True,
    }


# ---------------------------------------------------------------------------
# Scraping inputs
# ---------------------------------------------------------------------------
# Logged-in browser session saved by login_and_save_state().
STATE_PATH = os.environ.get("IG_STATE_PATH", "ig_state.json")

# Hashtags to harvest post links from.
HASHTAGS = ["carnaval", "carnavalrecife", "recife"]

# Accounts whose STORIES you want to scrape (no leading @).
# These are the "select pages" — e.g. official health/event/news accounts.
STORY_PAGES = [
    "prefeituradorecife",
    "saude_recife",
    "carnaval",
]

# Accounts whose recent FEED POSTS you want to scrape (no leading @).
FEED_PAGES = [
    "prefeituradorecife",
    "saude_recife",
]

# Output files
POSTS_CSV = "carnaval_posts.csv"
STORIES_CSV = "carnaval_stories.csv"
RISK_CSV = "carnaval_posts_with_risk.csv"
STORIES_MEDIA_DIR = "stories_media"
OUTPUT_XLSX = "carnaval_output.xlsx"   # default Excel workbook for the GUI

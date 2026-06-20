import csv
from pathlib import Path
from typing import List

from app.llm.analyzer import AnalysisResult

COLUMNS = [
    "username",
    "city",
    "content_type",
    "probability",
    "reason",
    "text",
    "matched_keywords",
    "url",
]


def export(results: List[AnalysisResult], path: str) -> int:
    """Write results to CSV. Returns number of rows written."""
    out = Path(path)
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for r in results:
            writer.writerow({
                "username": r.username,
                "city": r.city,
                "content_type": r.content_type,
                "probability": r.probability,
                "reason": r.reason[:100],
                "text": r.text[:500],
                "matched_keywords": "; ".join(r.matched_keywords),
                "url": r.url,
            })
    return len(results)

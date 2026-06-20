import json
from dataclasses import dataclass, field, asdict, fields as dc_fields
from pathlib import Path
from typing import List

CONFIG_PATH = Path("config.json")


@dataclass
class AccountConfig:
    username: str
    scrape_posts: bool = True
    scrape_stories: bool = True
    scrape_reels: bool = True


@dataclass
class Config:
    ig_username: str = ""
    ig_password: str = ""
    state_path: str = "ig_state.json"
    ollama_endpoint: str = "http://localhost:11434"
    text_model: str = "qwen2.5:7b"
    vision_model: str = "gemma3:4b"
    accounts: List[AccountConfig] = field(default_factory=list)
    keywords: List[str] = field(default_factory=lambda: [
        "febre", "surto", "intoxicação", "vômito", "diarreia",
        "doença", "infectado", "contaminação", "hospital", "epidemia",
        "sintomas", "náusea", "mal-estar", "internação", "urgência",
        "dengue", "leptospirose", "gripe", "covid", "zika",
    ])
    max_posts_per_account: int = 50
    analyze_all: bool = False
    min_probability: int = 2

    def save(self) -> None:
        data = asdict(self)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_PATH.exists():
            cfg = cls()
            cfg.save()
            return cfg
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
            accounts_data = data.pop("accounts", [])
            accounts = [AccountConfig(**a) for a in accounts_data]
            known = {f.name for f in dc_fields(cls)}
            cfg = cls(**{k: v for k, v in data.items() if k in known})
            cfg.accounts = accounts
            return cfg
        except Exception:
            cfg = cls()
            cfg.save()
            return cfg

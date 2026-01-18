#!/usr/bin/env python3
from __future__ import annotations

"""
meta_weekly_refresh.py
version: 1.3.0
date: 18-JAN-2026

Scrapea Standard BO1 desde AetherHub y solo actualiza:

Meta/standard/decks.json
Meta/standard/index.json

Archiva versiones anteriores como:
  decks_dd-MON-yyyy.json.gz
  index_dd-MON-yyyy.json.gz
  _manifest_dd-MON-yyyy.json.gz
"""

import gzip
import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

# ============== CONFIG ==============

VERSION = "1.3.0"
OUT_ROOT = "Meta"
FORMAT_KEY = "standard"   # interno
SCRAPER_FORMAT_NAME = "Standard BO1"

BASIC_LANDS = {
    "Plains", "Island", "Swamp", "Mountain", "Forest",
    "Snow-Covered Plains", "Snow-Covered Island", "Snow-Covered Swamp",
    "Snow-Covered Mountain", "Snow-Covered Forest",
}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


# ============== UTILIDADES ==============

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dd_mon_yyyy(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return dt.strftime("%d-%b-%Y").upper()


def stable_id(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def gzip_file(src: str, dst: str) -> None:
    with open(src, "rb") as f_in, gzip.open(dst, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)


def archive_if_exists(path: str, suffix: str) -> None:
    if not os.path.exists(path):
        return
    base_dir = os.path.dirname(path)
    base_name = os.path.basename(path)
    stem, ext = os.path.splitext(base_name)
    archived_json = os.path.join(base_dir, f"{stem}_{suffix}{ext}")
    archived_gz = archived_json + ".gz"
    shutil.move(path, archived_json)
    gzip_file(archived_json, archived_gz)
    os.remove(archived_json)


def atomic_write_json(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ============== SCRAPER (tu lógica adaptada) ==============

class MTGAMetaScraper:
    BASE_URL = "https://aetherhub.com/Metagame"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA})

    def fetch_page(self, url: str) -> Optional[BeautifulSoup]:
        try:
            r = self.session.get(url, timeout=12)
            r.raise_for_status()
            return BeautifulSoup(r.content, "html.parser")
        except requests.RequestException:
            return None

    def _extract_cards(self, soup: BeautifulSoup) -> List[Dict]:
        cards: List[Dict] = []
        main_section = soup.find(
            string=lambda text: text and "Main" in text and "cards" in text
        )
        if not main_section:
            return cards
        table = main_section.find_next("table")
        if not table:
            return cards
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if not cells:
                continue
            first = cells[0].get_text(strip=True)
            parts = first.split(maxsplit=1)
            if len(parts) != 2 or not parts[0].isdigit():
                continue
            qty = int(parts[0])
            name = parts[1]
            cards.append({"name": name, "quantity": qty})
        return cards

    def parse_standard_bo1(self, max_decks: int = 60) -> Dict:
        url = f"{self.BASE_URL}/Standard-BO1/"
        soup = self.fetch_page(url)
        if not soup:
            return {
                "version": VERSION,
                "date": dd_mon_yyyy(),
                "updatedAt": now_iso(),
                "format": "standard",
                "source": "aetherhub",
                "decks": [],
            }

        decks: List[Dict] = []
        rows = soup.find_all("tr")
        count = 0

        for row in rows:
            if count >= max_decks:
                break
            link = row.find("a")
            if not link or "/Deck/" not in link.get("href", ""):
                continue
            deck_link = row.find("a", href=lambda x: x and "/Deck/" in x)
            if not deck_link:
                continue
            deck_name = deck_link.get_text(strip=True)
            deck_url = deck_link.get("href")
            full_url = (
                f"https://aetherhub.com{deck_url}"
                if deck_url and deck_url.startswith("/")
                else deck_url
            )
            if not full_url:
                continue
            deck_soup = self.fetch_page(full_url)
            if not deck_soup:
                continue
            cards = self._extract_cards(deck_soup)
            if not cards:
                continue
            decks.append(
                {
                    "name": deck_name,
                    "url": full_url,
                    "cards": cards,
                }
            )
            count += 1

        return {
            "version": VERSION,
            "date": dd_mon_yyyy(),
            "updatedAt": now_iso(),
            "format": "standard",
            "source": "aetherhub",
            "decks": decks,
        }


# ============== CONVERSIÓN A Meta/standard/*.json ==============

@dataclass
class DeckMeta:
    deckId: str
    format: str
    archetype: Optional[str]
    commander: Optional[str]
    source: str
    sourceUrl: str
    updatedAt: str
    arenaImport: str
    mainCards: List[Dict]
    sideboardCards: List[Dict]
    signature: List[str]


def build_signature(cards: List[Dict], k: int = 20) -> List[str]:
    filtered = [c for c in cards if c["name"] not in BASIC_LANDS]
    filtered.sort(key=lambda x: (-x["quantity"], x["name"]))
    return [c["name"] for c in filtered[:k]]


def scraper_to_meta(standard_meta: Dict) -> Dict[str, Dict]:
    updated_at = standard_meta.get("updatedAt", now_iso())
    suffix = standard_meta.get("date", dd_mon_yyyy())

    decks_meta: List[DeckMeta] = []
    by_card: Dict[str, List[str]] = {}
    by_archetype: Dict[str, List[str]] = {}
    by_commander: Dict[str, str] = {}

    for deck in standard_meta.get("decks", []):
        cards = deck.get("cards", [])
        if not cards:
            continue

        # Arena import simple (sin set/cn)
        lines = ["Deck"]
        main_cards: List[Dict] = []
        for c in cards:
            q = int(c["quantity"])
            name = c["name"]
            lines.append(f"{q} {name}")
            main_cards.append({"name": name, "count": q})
        arena_import = "\n".join(lines)

        deck_id = stable_id(deck.get("url", "") or deck.get("name", ""))
        archetype = deck.get("name")
        sig = build_signature(cards, 20)

        dm = DeckMeta(
            deckId=deck_id,
            format=FORMAT_KEY,
            archetype=archetype,
            commander=None,
            source="aetherhub",
            sourceUrl=deck.get("url", ""),
            updatedAt=updated_at,
            arenaImport=arena_import,
            mainCards=main_cards,
            sideboardCards=[],
            signature=sig,
        )
        decks_meta.append(dm)

        for c in cards:
            name = c["name"]
            if name in BASIC_LANDS:
                continue
            by_card.setdefault(name, []).append(deck_id)

        if archetype:
            by_archetype.setdefault(archetype, []).append(deck_id)

    decks_obj = {
        "version": VERSION,
        "date": suffix,
        "updatedAt": updated_at,
        "format": FORMAT_KEY,
        "source": "aetherhub",
        "decks": [asdict(d) for d in decks_meta],
    }

    index_obj = {
        "version": VERSION,
        "date": suffix,
        "updatedAt": updated_at,
        "format": FORMAT_KEY,
        "source": "aetherhub",
        "byCard": by_card,
        "byArchetype": by_archetype,
        "byCommander": by_commander,
    }

    return {"decks_obj": decks_obj, "index_obj": index_obj}


# ============== MAIN ==============

def main() -> None:
    suffix = dd_mon_yyyy()
    updated_at = now_iso()

    scraper = MTGAMetaScraper()
    standard_meta = scraper.parse_standard_bo1(max_decks=60)
    meta = scraper_to_meta(standard_meta)

    ensure_dir(OUT_ROOT)
    fmt_dir = os.path.join(OUT_ROOT, FORMAT_KEY)
    ensure_dir(fmt_dir)

    decks_path = os.path.join(fmt_dir, "decks.json")
    index_path = os.path.join(fmt_dir, "index.json")

    archive_if_exists(decks_path, suffix)
    archive_if_exists(index_path, suffix)

    atomic_write_json(decks_path, meta["decks_obj"])
    atomic_write_json(index_path, meta["index_obj"])

    manifest = {
        "version": VERSION,
        "date": suffix,
        "updatedAt": updated_at,
        "formats": [FORMAT_KEY],
        "outputs": {
            FORMAT_KEY: {
                "decks": len(meta["decks_obj"]["decks"]),
                "uniqueCards": len(meta["index_obj"]["byCard"]),
            }
        },
    }

    manifest_path = os.path.join(OUT_ROOT, "_manifest.json")
    archive_if_exists(manifest_path, suffix)
    atomic_write_json(manifest_path, manifest)

    print(json.dumps(manifest["outputs"], ensure_ascii=False))


if __name__ == "__main__":
    main()

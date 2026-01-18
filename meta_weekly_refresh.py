#!/usr/bin/env python3
from __future__ import annotations

"""
meta_weekly_refresh.py
version: 1.1.0
date: 18-JAN-2026

Scrapea MTG Arena meta desde AetherHub usando la lógica de MTGAMetaScraper
y genera:

Meta/standard/decks.json
Meta/standard/index.json
Meta/historic/decks.json
Meta/historic/index.json
Meta/brawl/decks.json
Meta/brawl/index.json

Cada vez que corre:
- renombra decks.json -> decks_dd-MON-yyyy.json.gz
- renombra index.json -> index_dd-MON-yyyy.json.gz
- renombra _manifest.json -> _manifest_dd-MON-yyyy.json.gz
"""

import csv
import gzip
import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

# =========================
# CONFIG
# =========================

VERSION = "1.1.0"
OUT_ROOT = "Meta"

FORMATS_MAP = {
    # nombre que usa el scraper -> clave interna
    "Standard BO1": "standard",
    "Historic BO1": "historic",
    # Brawl/Alchemy se pueden usar más adelante, por ahora se ignoran o se agregan si quieres
    "Alchemy BO1": "alchemy",
}

LIMITS = {
    "standard": 60,
    "historic": 60,
    "brawl": 0,      # de momento 0 porque tu scraper aún no cubre Brawl
    "alchemy": 20,   # si decides usarlo
}

BASE_META_URL = "https://aetherhub.com/Metagame"

SCRAPER_FORMATS = {
    "Standard BO1": "/Standard-BO1/",
    "Alchemy BO1": "/Alchemy-BO1/",
    "Historic BO1": "/Historic-BO1/",
}

BASIC_LANDS = {
    "Plains", "Island", "Swamp", "Mountain", "Forest",
    "Snow-Covered Plains", "Snow-Covered Island", "Snow-Covered Swamp",
    "Snow-Covered Mountain", "Snow-Covered Forest",
}

REQ_TIMEOUT = 20
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


# =========================
# UTILIDADES GENERALES
# =========================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dd_mon_yyyy(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return dt.strftime("%d-%b-%Y").upper()


def stable_id(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def gzip_file(src_path: str, dst_path: str) -> None:
    with open(src_path, "rb") as f_in, gzip.open(dst_path, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)


def archive_if_exists(path: str, suffix: str) -> None:
    """
    Renombra archivo actual agregando _SUFFIX y lo comprime a .gz.
    Ej: decks.json -> decks_18-JAN-2026.json.gz
    """
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


# =========================
# SCRAPER (basado en tu MTGAMetaScraper)
# =========================

class MTGAMetaScraper:
    BASE_URL = BASE_META_URL
    FORMATS = SCRAPER_FORMATS

    def __init__(self, timeout: int = 10):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": UA
        })
        self.timeout = timeout
        self.decks_data: Dict[str, List[Dict]] = {}

    def fetch_page(self, url: str) -> Optional[BeautifulSoup]:
        try:
            r = self.session.get(url, timeout=self.timeout)
            r.raise_for_status()
            return BeautifulSoup(r.content, "html.parser")
        except requests.RequestException:
            return None

    def extract_deck_links(self, soup: BeautifulSoup) -> List[Dict[str, str]]:
        decks: List[Dict[str, str]] = []
        deck_rows = soup.find_all("tr")
        for row in deck_rows:
            link = row.find("a")
            if not link:
                continue
            href = link.get("href", "")
            if "/Deck/" not in href:
                continue
            name = link.get_text(strip=True)
            if not name:
                continue
            url = f"https://aetherhub.com{href}" if href.startswith("/") else href
            decks.append({"name": name, "url": url})
        return decks

    def parse_deck_page(self, soup: BeautifulSoup) -> Dict:
        deck_data: Dict = {}

        header = soup.find("h1") or soup.find("h2")
        if header:
            deck_data["name"] = header.get_text(strip=True)

        meta_text = soup.find(string=lambda t: t and "% of meta" in t)
        if meta_text:
            try:
                pct = float(meta_text.split()[0])
                deck_data["meta_percentage"] = pct
            except (ValueError, IndexError):
                deck_data["meta_percentage"] = None
        else:
            deck_data["meta_percentage"] = None

        cards: List[Dict] = []

        # Heurística: buscar tablas de main deck
        tables = soup.find_all("table")
        for table in tables:
            for row in table.find_all("tr"):
                cells = row.find_all(["td", "th"])
                if len(cells) < 1:
                    continue
                text = cells[0].get_text(strip=True)
                parts = text.split(maxsplit=1)
                if len(parts) != 2:
                    continue
                qty_str, name = parts
                if not qty_str.isdigit():
                    continue
                cards.append({
                    "quantity": int(qty_str),
                    "name": name
                })

        deck_data["cards"] = cards
        deck_data["total_cards"] = sum(c["quantity"] for c in cards)
        return deck_data

    def scrape_format(self, format_name: str, max_decks: int = 2) -> List[Dict]:
        path = self.FORMATS.get(format_name)
        if not path:
            return []
        url = f"{self.BASE_URL}{path}"
        soup = self.fetch_page(url)
        if not soup:
            return []

        deck_links = self.extract_deck_links(soup)
        out: List[Dict] = []
        for info in deck_links[:max_decks]:
            dsoup = self.fetch_page(info["url"])
            if not dsoup:
                continue
            data = self.parse_deck_page(dsoup)
            data["url"] = info["url"]
            data["format"] = format_name
            out.append(data)
        return out

    def scrape_all(self, max_decks_per_format: int = 2) -> Dict[str, List[Dict]]:
        all_decks: Dict[str, List[Dict]] = {}
        for fmt in self.FORMATS.keys():
            all_decks[fmt] = self.scrape_format(fmt, max_decks_per_format)
        self.decks_data = all_decks
        return all_decks


# =========================
# CONVERSIÓN A Meta/<format>/*
# =========================

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


def build_signature(main_cards: List[Dict], k: int = 20) -> List[str]:
    filtered = [c for c in main_cards if c["name"] not in BASIC_LANDS]
    filtered.sort(key=lambda x: (-c["count"], c["name"]))
    return [c["name"] for c in filtered[:k]]


def build_signature_safe(main_cards: List[Dict], k: int = 20) -> List[str]:
    filtered = [c for c in main_cards if c["name"] not in BASIC_LANDS]
    filtered.sort(key=lambda x: (-x["quantity"], x["name"]))
    return [c["name"] for c in filtered[:k]]


def convert_scraper_output_to_meta(
    scraper_data: Dict[str, List[Dict]],
    updated_at: str,
    suffix: str,
) -> Dict[str, Dict[str, object]]:
    """
    Devuelve un dict:
      {
        "standard": {"decks_obj": ..., "index_obj": ...},
        "historic": {...},
        "alchemy": {...}
      }
    (solo llenará los formatos presentes en scraper_data)
    """
    result: Dict[str, Dict[str, object]] = {}

    for scraper_fmt, decks in scraper_data.items():
        internal_fmt = FORMATS_MAP.get(scraper_fmt)
        if not internal_fmt:
            continue

        max_decks = LIMITS.get(internal_fmt, 0)
        if max_decks == 0:
            continue

        decks_meta: List[DeckMeta] = []
        by_card: Dict[str, List[str]] = {}
        by_archetype: Dict[str, List[str]] = {}
        by_commander: Dict[str, str] = {}

        for deck in decks[:max_decks]:
            cards = deck.get("cards", [])
            if not cards:
                continue

            # arenaImport simple: sin set/cn, MTGA lo acepta
            lines = ["Deck"]
            main_cards: List[Dict] = []
            for c in cards:
                q = int(c["quantity"])
                name = c["name"]
                lines.append(f"{q} {name}")
                main_cards.append({"name": name, "count": q})

            arena_import = "\n".join(lines)

            did = stable_id(deck.get("url", "") or deck.get("name", "") or scraper_fmt)
            archetype = deck.get("name")
            commander = None  # solo relevante para Brawl

            signature = build_signature_safe(cards, 20)

            dm = DeckMeta(
                deckId=did,
                format=internal_fmt,
                archetype=archetype,
                commander=commander,
                source="aetherhub",
                sourceUrl=deck.get("url", ""),
                updatedAt=updated_at,
                arenaImport=arena_import,
                mainCards=main_cards,
                sideboardCards=[],
                signature=signature,
            )
            decks_meta.append(dm)

            # index
            for c in cards:
                name = c["name"]
                if name in BASIC_LANDS:
                    continue
                by_card.setdefault(name, []).append(did)

            if internal_fmt != "brawl":
                if archetype:
                    by_archetype.setdefault(archetype, []).append(did)
            # si agregas Brawl, aquí se rellenaría by_commander

        decks_obj = {
            "version": VERSION,
            "date": suffix,
            "updatedAt": updated_at,
            "format": internal_fmt,
            "source": "aetherhub",
            "decks": [asdict(d) for d in decks_meta],
        }

        index_obj = {
            "version": VERSION,
            "date": suffix,
            "updatedAt": updated_at,
            "format": internal_fmt,
            "source": "aetherhub",
            "byCard": by_card,
            "byArchetype": by_archetype,
            "byCommander": by_commander,
        }

        result[internal_fmt] = {
            "decks_obj": decks_obj,
            "index_obj": index_obj,
        }

    return result


# =========================
# MAIN
# =========================

def main() -> None:
    suffix = dd_mon_yyyy()
    updated_at = now_iso()

    ensure_dir(OUT_ROOT)

    # 1) Scrape meta con tu lógica
    scraper = MTGAMetaScraper(timeout=15)
    data = scraper.scrape_all(max_decks_per_format=LIMITS["standard"])

    # 2) Convertir a estructura Meta/
    meta_per_format = convert_scraper_output_to_meta(data, updated_at, suffix)

    manifest = {
        "version": VERSION,
        "date": suffix,
        "updatedAt": updated_at,
        "sources": {"aetherhub_formats": list(SCRAPER_FORMATS.keys())},
        "limits": LIMITS,
        "outputs": {},
    }

    for fmt in ("standard", "historic", "alchemy", "brawl"):
        if fmt not in meta_per_format:
            continue

        fmt_dir = os.path.join(OUT_ROOT, fmt)
        ensure_dir(fmt_dir)

        decks_path = os.path.join(fmt_dir, "decks.json")
        index_path = os.path.join(fmt_dir, "index.json")

        archive_if_exists(decks_path, suffix)
        archive_if_exists(index_path, suffix)

        decks_obj = meta_per_format[fmt]["decks_obj"]
        index_obj = meta_per_format[fmt]["index_obj"]

        atomic_write_json(decks_path, decks_obj)
        atomic_write_json(index_path, index_obj)

        manifest["outputs"][fmt] = {
            "decks": len(decks_obj["decks"]),
            "uniqueCards": len(index_obj["byCard"]),
        }

    manifest_path = os.path.join(OUT_ROOT, "_manifest.json")
    archive_if_exists(manifest_path, suffix)
    atomic_write_json(manifest_path, manifest)

    print(json.dumps(manifest["outputs"], ensure_ascii=False))


if __name__ == "__main__":
    main()

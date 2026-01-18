#!/usr/bin/env python3
from __future__ import annotations

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
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# =========================
# CONFIG v1.0.2
# =========================
VERSION = "1.0.2"
OUT_ROOT = "Meta"  # carpeta en tu repo

LIMITS = {
    "standard": 60,
    "historic": 60,
    "brawl": 100,  # 1 deck por commander
}

BASE = "https://aetherhub.com"

# Seeds (BO1 only)
SEEDS = {
    "standard": [
        "https://aetherhub.com/Metagame/Standard-BO1/",
        "https://aetherhub.com/Decks/Standard-BO1/",
        "https://aetherhub.com/Meta/",
    ],
    "historic": [
        "https://aetherhub.com/Metagame/Historic-BO1/",
        "https://aetherhub.com/Decks/Historic-BO1/",
    ],
    "brawl": [
        "https://aetherhub.com/MTGA-Decks/Brawl/",
        "https://aetherhub.com/Metagame/Brawl/",
        "https://aetherhub.com/Decks/Brawl/",
    ],
}

BASIC_LANDS = {
    "Plains", "Island", "Swamp", "Mountain", "Forest",
    "Snow-Covered Plains", "Snow-Covered Island", "Snow-Covered Swamp",
    "Snow-Covered Mountain", "Snow-Covered Forest",
}

REQ_TIMEOUT = 40
SLEEP_SECS = 1.0

# User-Agent "browser-like" para reducir bloqueos anti-bot
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class Deck:
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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dd_mon_yyyy(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return dt.strftime("%d-%b-%Y").upper()


def stable_id(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def http_get(session: requests.Session, url: str) -> str:
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    r = session.get(url, headers=headers, timeout=REQ_TIMEOUT)
    r.raise_for_status()
    return r.text


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def gzip_file(src_path: str, dst_path: str) -> None:
    with open(src_path, "rb") as f_in, gzip.open(dst_path, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)


def archive_if_exists(path: str, suffix: str) -> None:
    """
    Renombra el archivo actual agregando _SUFFIX y lo comprime a .gz.
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


def absolute(url_or_path: str) -> str:
    return urljoin(BASE, url_or_path)


def extract_links_for_decks(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    hrefs = []
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href:
            continue

        # patrones típicos de deck pages en AetherHub
        if href.startswith("/Deck/"):
            hrefs.append(href)
        elif "/Metagame/" in href and "/Deck/" in href:
            hrefs.append(href)

    return list(dict.fromkeys(hrefs))


def sanitize_arena_import(raw: str) -> str:
    if not raw:
        return ""

    lines = raw.splitlines()
    header_re = re.compile(r"^(Deck|Sideboard|Commander|Companion)$", re.I)
    card_re = re.compile(r"^\s*\d+\s+.+")

    out = []
    has_deck = False

    for line in lines:
        t = line.strip().replace("`", "").replace("**", "").strip()
        if not t:
            continue

        if header_re.match(t):
            norm = t[:1].upper() + t[1:].lower()
            out.append(norm)
            if norm.lower() == "deck":
                has_deck = True
            continue

        if card_re.match(t):
            out.append(t)

    if out and not has_deck:
        out.insert(0, "Deck")

    return "\n".join(out).strip()


def extract_decklist_from_html(html: str) -> str:
    """Best-effort: textarea/pre/code + regex fallback."""
    soup = BeautifulSoup(html, "html.parser")

    for ta in soup.find_all("textarea"):
        txt = (ta.get_text() or "").strip()
        if txt.startswith("Deck"):
            return txt

    for tag in soup.find_all(["pre", "code"]):
        txt = (tag.get_text() or "").strip()
        if txt.startswith("Deck"):
            return txt

    # decklist embebido como string escapado
    m = re.search(r"(Deck\\n(?:.|\\n)+?)\"", html)
    if m:
        blob = m.group(1)
        blob = blob.replace("\\n", "\n").replace("\\r", "")
        blob = blob.replace('\\"', '"')
        return blob

    return ""


def parse_arena_lines(arena: str) -> Tuple[List[Dict], List[Dict]]:
    main, sb = [], []
    section = "main"

    for line in arena.splitlines():
        t = line.strip()
        if not t:
            continue

        if t.lower() == "deck":
            section = "main"
            continue
        if t.lower() in ("sideboard", "commander"):
            section = "sb"
            continue

        m = re.match(r"^(\d+)\s+(.+?)\s*(\([A-Z0-9]+\)\s+\d+)?$", t)
        if not m:
            continue

        count = int(m.group(1))
        name = m.group(2).strip()
        entry = {"name": name, "count": count}

        (main if section == "main" else sb).append(entry)

    return main, sb


def build_signature(main_cards: List[Dict], k: int = 20) -> List[str]:
    filtered = [c for c in main_cards if c["name"] not in BASIC_LANDS]
    filtered.sort(key=lambda x: (-x["count"], x["name"]))
    return [c["name"] for c in filtered[:k]]


def extract_h1_title(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    if not h1:
        return None
    t = h1.get_text(" ", strip=True)
    return t or None


def extract_commander_from_arena(arena: str) -> Optional[str]:
    lines = [ln.strip() for ln in arena.splitlines() if ln.strip()]
    for i, ln in enumerate(lines):
        if ln.lower() == "commander" and i + 1 < len(lines):
            nxt = lines[i + 1]
            m = re.match(r"^(\d+)\s+(.+?)\s*(\([A-Z0-9]+\)\s+\d+)?$", nxt)
            if m:
                return m.group(2).strip()
    return None


def build_format(session: requests.Session, fmt: str, updated_at: str) -> Tuple[Dict, Dict]:
    suffix = dd_mon_yyyy()
    max_decks = LIMITS[fmt]

    # 1) discovery
    deck_urls: List[str] = []
    for seed in SEEDS[fmt]:
        try:
            html = http_get(session, seed)
            for href in extract_links_for_decks(html):
                deck_urls.append(absolute(href))
            time.sleep(SLEEP_SECS)
        except Exception:
            continue

    deck_urls = list(dict.fromkeys(deck_urls))

    # 2) ingest + index
    decks: List[Deck] = []
    by_card: Dict[str, List[str]] = {}
    by_archetype: Dict[str, List[str]] = {}
    by_commander: Dict[str, str] = {}

    seen_commander = set()

    for url in deck_urls:
        if fmt != "brawl" and len(decks) >= max_decks:
            break
        if fmt == "brawl" and len(by_commander) >= max_decks:
            break

        try:
            html = http_get(session, url)
            raw = extract_decklist_from_html(html)
            arena = sanitize_arena_import(raw)
            if not arena.startswith("Deck"):
                continue

            main_cards, sb_cards = parse_arena_lines(arena)
            if not main_cards:
                continue

            did = stable_id(url)
            sig = build_signature(main_cards, 20)

            archetype = None
            commander = None

            if fmt == "brawl":
                commander = (
                    extract_commander_from_arena(arena)
                    or extract_h1_title(html)
                    or "Unknown Commander"
                )
                if commander in seen_commander:
                    continue
                seen_commander.add(commander)
            else:
                archetype = extract_h1_title(html)

            d = Deck(
                deckId=did,
                format=fmt,
                archetype=archetype,
                commander=commander,
                source="aetherhub",
                sourceUrl=url,
                updatedAt=updated_at,
                arenaImport=arena,
                mainCards=main_cards,
                sideboardCards=sb_cards,  # BO1 normalmente vacío
                signature=sig,
            )
            decks.append(d)

            for c in main_cards:
                name = c["name"]
                if name in BASIC_LANDS:
                    continue
                by_card.setdefault(name, []).append(did)

            if fmt == "brawl":
                by_commander[commander] = did
            else:
                if archetype:
                    by_archetype.setdefault(archetype, []).append(did)

            time.sleep(SLEEP_SECS)
        except Exception:
            continue

    decks_obj = {
        "version": VERSION,
        "date": suffix,
        "updatedAt": updated_at,
        "format": fmt,
        "source": "aetherhub",
        "decks": [asdict(d) for d in decks],
    }

    index_obj = {
        "version": VERSION,
        "date": suffix,
        "updatedAt": updated_at,
        "format": fmt,
        "source": "aetherhub",
        "byCard": by_card,
        "byArchetype": by_archetype,
        "byCommander": by_commander,
    }

    return decks_obj, index_obj


def main() -> None:
    suffix = dd_mon_yyyy()
    updated_at = now_iso()

    ensure_dir(OUT_ROOT)

    manifest = {
        "version": VERSION,
        "date": suffix,
        "updatedAt": updated_at,
        "sources": {k: "aetherhub" for k in SEEDS.keys()},
        "limits": LIMITS,
        "outputs": {},
    }

    with requests.Session() as session:
        for fmt in ("standard", "historic", "brawl"):
            fmt_dir = os.path.join(OUT_ROOT, fmt)
            ensure_dir(fmt_dir)

            decks_path = os.path.join(fmt_dir, "decks.json")
            index_path = os.path.join(fmt_dir, "index.json")

            # Archiva lo anterior y lo comprime
            archive_if_exists(decks_path, suffix)
            archive_if_exists(index_path, suffix)

            decks_obj, index_obj = build_format(session, fmt, updated_at)

            atomic_write_json(decks_path, decks_obj)
            atomic_write_json(index_path, index_obj)

            manifest["outputs"][fmt] = {
                "decks": len(decks_obj["decks"]),
                "uniqueCards": len(index_obj["byCard"]),
                "uniqueCommanders": len(index_obj["byCommander"]),
            }

    manifest_path = os.path.join(OUT_ROOT, "_manifest.json")
    archive_if_exists(manifest_path, suffix)
    atomic_write_json(manifest_path, manifest)

    print(json.dumps(manifest["outputs"], ensure_ascii=False))


if __name__ == "__main__":
    main()

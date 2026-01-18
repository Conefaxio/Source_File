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
# CONFIG
# =========================
VERSION = "1.0.0"
OUT_ROOT = "Meta"  # tu carpeta del repo

LIMITS = {
    "standard": 60,
    "historic": 60,
    "brawl": 100,  # 1 deck por commander
}

SOURCES = {
    "standard": {
        "type": "mtgdecks",
        "list_url": "https://mtgdecks.net/Standard/arena",
    },
    "historic": {
        "type": "mtgdecks",
        "list_url": "https://mtgdecks.net/Historic/arena",
    },
    "brawl": {
        "type": "aetherhub",
        # deck discovery (lista grande de decks recientes)
        "list_url": "https://aetherhub.com/MTGA-Decks/Brawl/",
    },
}

BASIC_LANDS = {
    "Plains", "Island", "Swamp", "Mountain", "Forest",
    "Snow-Covered Plains", "Snow-Covered Island", "Snow-Covered Swamp",
    "Snow-Covered Mountain", "Snow-Covered Forest",
}

UA = "mtga-meta-cron/1.0 (github actions)"
REQ_TIMEOUT = 35
SLEEP_SECS = 1.0

# =========================
# DATA
# =========================
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


# =========================
# UTILS
# =========================
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def dd_mon_yyyy(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    # dd-MON-yyyy (MON en inglés, mayúsculas)
    return dt.strftime("%d-%b-%Y").upper()

def stable_id(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]

def http_get(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=REQ_TIMEOUT)
    r.raise_for_status()
    return r.text

def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)

def gzip_file(src_path: str, dst_path: str) -> None:
    with open(src_path, "rb") as f_in, gzip.open(dst_path, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)

def archive_if_exists(path: str, suffix: str) -> None:
    """
    Si existe Meta/x/file.json -> lo renombra a file_SUFFIX.json y lo comprime a .json.gz
    Luego elimina el .json sin comprimir.
    """
    if not os.path.exists(path):
        return

    base_dir = os.path.dirname(path)
    base_name = os.path.basename(path)  # decks.json
    stem, ext = os.path.splitext(base_name)  # decks , .json
    archived_json = os.path.join(base_dir, f"{stem}_{suffix}{ext}")
    archived_gz = archived_json + ".gz"

    # mover el actual -> archived_json
    shutil.move(path, archived_json)
    # gzip -> archived_gz
    gzip_file(archived_json, archived_gz)
    # borrar el archived_json plano
    os.remove(archived_json)

def atomic_write_json(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

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


# =========================
# MTGDECKS
# =========================
def mtgdecks_discover_deck_urls(list_url: str, fmt_slug: str) -> List[str]:
    html = http_get(list_url)
    soup = BeautifulSoup(html, "html.parser")

    urls = []
    for a in soup.select("a[href]"):
        href = a.get("href", "").strip()
        if not href:
            continue

        # deck pages suelen ser /Standard/<slug>-<id> o /Historic/<slug>-<id>
        if href.startswith(f"/{fmt_slug}/") and not href.endswith("/arena") and "decklists" not in href:
            urls.append(urljoin(list_url, href))

    # dedupe preservando orden
    urls = list(dict.fromkeys(urls))
    return urls

def mtgdecks_extract_arena_deck_from_page(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    # 1) textarea con "Deck"
    for ta in soup.find_all("textarea"):
        txt = (ta.get_text() or "").strip()
        if txt.startswith("Deck") and "\n" in txt:
            return txt

    # 2) pre/code
    for tag in soup.find_all(["pre", "code"]):
        txt = (tag.get_text() or "").strip()
        if txt.startswith("Deck") and "\n" in txt:
            return txt

    # 3) buscar bloque "Deck" en el HTML (fallback)
    m = re.search(r"(Deck\\n(?:.|\\n)+?)\"", html)
    if m:
        blob = m.group(1).replace("\\n", "\n").replace("\\r", "").replace('\\"', '"')
        return blob

    return ""


# =========================
# AETHERHUB (BRAWL)
# =========================
def aetherhub_discover_brawl_urls(list_url: str) -> List[str]:
    html = http_get(list_url)
    soup = BeautifulSoup(html, "html.parser")

    urls = []
    for a in soup.select("a[href]"):
        href = a.get("href", "").strip()
        if not href:
            continue

        # en AetherHub, páginas de deck a veces son /Deck/<id>/...
        if href.startswith("/Deck/"):
            urls.append(urljoin(list_url, href))

        # y a veces /Metagame/.../Deck/...
        if "/Metagame/" in href and "/Deck/" in href:
            urls.append(urljoin(list_url, href))

    urls = list(dict.fromkeys(urls))
    return urls

def aetherhub_extract_deck_from_page(html: str) -> str:
    # muy similar al extractor anterior, tolerante a cambios
    soup = BeautifulSoup(html, "html.parser")

    for ta in soup.find_all("textarea"):
        txt = (ta.get_text() or "").strip()
        if txt.startswith("Deck"):
            return txt

    for tag in soup.find_all(["pre", "code"]):
        txt = (tag.get_text() or "").strip()
        if txt.startswith("Deck"):
            return txt

    m = re.search(r"(Deck\\n(?:.|\\n)+?)\"", html)
    if m:
        blob = m.group(1).replace("\\n", "\n").replace("\\r", "").replace('\\"', '"')
        return blob

    return ""

def extract_title(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    if h1:
        t = h1.get_text(" ", strip=True)
        return t or None
    return None


# =========================
# BUILD PER FORMAT
# =========================
def build_format(fmt: str, updated_at: str) -> Tuple[List[Deck], Dict]:
    cfg = SOURCES[fmt]
    max_decks = LIMITS[fmt]

    decks: List[Deck] = []
    by_card: Dict[str, List[str]] = {}
    by_archetype: Dict[str, List[str]] = {}
    by_commander: Dict[str, str] = {}

    if cfg["type"] == "mtgdecks":
        fmt_slug = "Standard" if fmt == "standard" else "Historic"
        deck_urls = mtgdecks_discover_deck_urls(cfg["list_url"], fmt_slug)
        deck_urls = deck_urls[: max_decks * 3]  # margen por fallos
        for url in deck_urls:
            if len(decks) >= max_decks:
                break
            try:
                html = http_get(url)
                raw = mtgdecks_extract_arena_deck_from_page(html)
                arena = sanitize_arena_import(raw)
                if not arena.startswith("Deck"):
                    continue

                main_cards, sb_cards = parse_arena_lines(arena)
                title = extract_title(html)
                archetype = title

                did = stable_id(url)
                sig = build_signature(main_cards, 20)

                d = Deck(
                    deckId=did,
                    format=fmt,
                    archetype=archetype,
                    commander=None,
                    source="mtgdecks",
                    sourceUrl=url,
                    updatedAt=updated_at,
                    arenaImport=arena,
                    mainCards=main_cards,
                    sideboardCards=sb_cards,
                    signature=sig,
                )
                decks.append(d)

                for c in main_cards:
                    name = c["name"]
                    if name in BASIC_LANDS:
                        continue
                    by_card.setdefault(name, []).append(did)

                if archetype:
                    by_archetype.setdefault(archetype, []).append(did)

                time.sleep(SLEEP_SECS)
            except Exception:
                continue

    elif cfg["type"] == "aetherhub":
        deck_urls = aetherhub_discover_brawl_urls(cfg["list_url"])

        seen_commander = set()
        for url in deck_urls:
            if len(decks) >= max_decks:
                break
            try:
                html = http_get(url)
                raw = aetherhub_extract_deck_from_page(html)
                arena = sanitize_arena_import(raw)
                if not arena.startswith("Deck"):
                    continue

                main_cards, sb_cards = parse_arena_lines(arena)

                # Heurística: H1 suele traer nombre del deck/commander
                commander = extract_title(html) or "Unknown Commander"
                if commander in seen_commander:
                    continue
                seen_commander.add(commander)

                did = stable_id(url)
                sig = build_signature(main_cards, 20)

                d = Deck(
                    deckId=did,
                    format=fmt,
                    archetype=None,
                    commander=commander,
                    source="aetherhub",
                    sourceUrl=url,
                    updatedAt=updated_at,
                    arenaImport=arena,
                    mainCards=main_cards,
                    sideboardCards=sb_cards,
                    signature=sig,
                )
                decks.append(d)

                for c in main_cards:
                    name = c["name"]
                    if name in BASIC_LANDS:
                        continue
                    by_card.setdefault(name, []).append(did)

                by_commander[commander] = did

                time.sleep(SLEEP_SECS)
            except Exception:
                continue

    index = {
        "version": VERSION,
        "date": dd_mon_yyyy(),
        "updatedAt": updated_at,
        "format": fmt,
        "source": cfg["type"],
        "byCard": by_card,
        "byArchetype": by_archetype,
        "byCommander": by_commander,
    }
    return decks, index


def main():
    suffix = dd_mon_yyyy()
    updated_at = now_iso()

    manifest = {
        "version": VERSION,
        "date": suffix,
        "updatedAt": updated_at,
        "sources": SOURCES,
        "limits": LIMITS,
        "outputs": {},
    }

    for fmt in ("standard", "historic", "brawl"):
        fmt_dir = os.path.join(OUT_ROOT, fmt)
        ensure_dir(fmt_dir)

        decks_path = os.path.join(fmt_dir, "decks.json")
        index_path = os.path.join(fmt_dir, "index.json")

        # Archiva lo anterior (si existe) y lo comprime
        archive_if_exists(decks_path, suffix)
        archive_if_exists(index_path, suffix)

        decks, index = build_format(fmt, updated_at)

        decks_obj = {
            "version": VERSION,
            "date": suffix,
            "updatedAt": updated_at,
            "format": fmt,
            "source": index["source"],
            "decks": [asdict(d) for d in decks],
        }

        atomic_write_json(decks_path, decks_obj)
        atomic_write_json(index_path, index)

        manifest["outputs"][fmt] = {
            "decks": len(decks),
            "uniqueCards": len(index["byCard"]),
        }

    # Manifest global en Meta/
    manifest_path = os.path.join(OUT_ROOT, "_manifest.json")
    archive_if_exists(manifest_path, suffix)
    atomic_write_json(manifest_path, manifest)

    print(json.dumps(manifest["outputs"], ensure_ascii=False))


if __name__ == "__main__":
    main()

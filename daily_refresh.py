# ============================================================
# VER        DATE            DETAIL
# 1.0        16-01-2026      SE AGREGA mtgaLegalSetsByFormat PARA MANEJO DE SETS POR FORMATO
# 1.1        16-01-2026      FIX - mtgaLegalSetsByFormat PARA MANEJO DE SETS POR FORMATO
# 1.2        16-01-2026      FIX - agrega SET en AllPrintings_MTGA_EN_ULTRA.json
# 1.3        16-01-2026      REFACTOR - ULTRA directo desde AllPrintings + Scryfall (IDs),
#                            refuerzo solo Standard/Historic/Brawl y regeneración si m.date > 31 días
# ============================================================

from __future__ import annotations

import re
import json
import gzip
import threading
import time
import sys
import os
import hashlib
from pathlib import Path
from datetime import datetime, UTC, timedelta
from collections import defaultdict, Counter
from typing import Dict, Any, List, Set

import requests

# ============================================================
# CI-FIRST SCRIPT (GitHub Actions friendly)
# ============================================================

# ---------------- CONFIG ----------------
# 1) ALLPRINTINGS (MTGJSON + SCRYFALL)
MTGJSON_URL = "https://mtgjson.com/api/v5/AllPrintings.json"
SCRYFALL_BULK_API = "https://api.scryfall.com/bulk-data"
SCRYFALL_DEFAULT_TYPE = "default_cards"
SCRYFALL_SEARCH_URL = "https://api.scryfall.com/cards/search"

MTGJSON_PATH = Path("AllPrintings.json")
SCRY_CACHE_DIR = Path("cache_scryfall")
SCRY_DEFAULT_PATH = SCRY_CACHE_DIR / "default_cards.json"

AP_OUT_JSON = Path("AllPrintings_MTGA_EN_ULTRA.json")
AP_OUT_GZ = Path("AllPrintings_MTGA_EN_ULTRA.json.gz")
AP_WRITE_GZ = True

# En CI, ideal: KEEP_DOWNLOADED=True + actions/cache
KEEP_DOWNLOADED = True

# Formatos foco / orden de la cadena de legalidades
KEEP_FORMATS = ["standard", "alchemy", "explorer", "historic", "timeless", "brawl"]

# Formatos para los que se hace refuerzo Scryfall (diff por ID)
FORMATS_TO_REINFORCE = ["standard", "historic", "brawl"]

# 2) COMPREHENSIVE RULES
COMP_RULES_TXT_URL = "https://media.wizards.com/2025/downloads/MagicCompRules%2020251114.txt"

CR_WRITE_GZ = True
OUT_NORMAL_JSON = Path("MagicCompRules.parsed.json")
OUT_ULTRA_FLAT_JSON = Path("MagicCompRules.ultra_flat.json")
OUT_ULTRA_FLAT_GZ = Path("MagicCompRules.ultra_flat.json.gz")
OUT_CONTROL_JSON = Path("MagicCompRules.control.json")

TOKENS = {
    "This is a state-based action.": "⟦SBA⟧",
    "See rule ": "⟦SR⟧",
    "in turn order": "⟦ITO⟧",
    "the active player": "⟦AP⟧",
    "nonactive player": "⟦NAP⟧",
}

# 3) BRAINS
BRAIN_FILES = ("gemini.brain.json", "grooq.brain.json")
NEW_BRAINS_DIR = Path("New_brains")

# ---------------- UTILS ----------------
def utc_now_z() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

def load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def dump_pretty_json(path: Path, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def dump_minified_json(path: Path, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))

def dump_minified_gzip_json(path: Path, obj: Any, compresslevel: int = 9) -> None:
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=compresslevel) as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))

def file_sha256(path: Path, chunk_size: int = 65536) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()

def file_line_count(path: Path) -> int:
    if path.suffix.lower() == ".gz":
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    with open(path, "rt", encoding="utf-8", errors="replace") as f:
        return sum(1 for _ in f)

def build_file_info(path: Path) -> dict:
    st = path.stat()
    return {
        "name": path.name,
        "path": str(path.resolve()),
        "sizeBytes": st.st_size,
        "sizeMB": round(st.st_size / 1024 / 1024, 4),
        "modifiedAtLocal": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        "sha256": file_sha256(path),
        "lineCount": file_line_count(path),
    }

# ---------------- "3 DOTS" ANIMATION ----------------
class Dots:
    def __init__(self, label: str):
        self.label = label
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        i = 0
        while not self._stop.is_set():
            dots = "." * (i % 4)
            sys.stdout.write(f"\r{self.label}{dots}   ")
            sys.stdout.flush()
            i += 1
            time.sleep(0.35)
        sys.stdout.write("\r" + " " * (len(self.label) + 6) + "\r")
        sys.stdout.flush()

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        self._thread.join(timeout=2)

# ---------------- GENERIC DOWNLOAD (stream) ----------------
def download_to(path: Path, url: str, timeout: int = 1800, label: str = "Descargando") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    with Dots(label):
        with requests.get(url, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
    tmp.replace(path)

# ---------------- BRAINS ----------------
def _unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    base = p.name
    i = 2
    while True:
        candidate = p.with_name(f"{base}_{i}")
        if not candidate.exists():
            return candidate
        i += 1

def _date_tag_full() -> str:
    return datetime.now(UTC).strftime("%d%m%Y")

def _date_tag_short() -> str:
    return datetime.now(UTC).strftime("%d%m%y")

def consume_new_brains(repo_root: Path) -> None:
    new_dir = repo_root / NEW_BRAINS_DIR
    if not new_dir.exists():
        print("ℹ️ New_brains/ no existe. No hay brains para consumir.")
        return

    moved_any = False
    date_full = _date_tag_full()
    date_short = _date_tag_short()

    for fname in BRAIN_FILES:
        src = new_dir / fname
        if not src.exists():
            continue

        dst = repo_root / fname

        if dst.exists():
            archived = _unique_path(repo_root / f"{fname}_{date_full}")
            dst.rename(archived)
            print(f"🗄️ Archivado root: {dst.name} -> {archived.name}")

        src.rename(dst)
        print(f"📦 Movido: {src} -> {dst}")
        moved_any = True

        marker = _unique_path(new_dir / f"{fname}_{date_short}_processed")
        marker.write_text("", encoding="utf-8")
        print(f"🏷️ Marker vacío: {marker}")

    if not moved_any:
        print("ℹ️ No se encontraron brains nuevos para mover.")

# ---------------- ALLPRINTINGS: MTGJSON + SCRYFALL ----------------
def ensure_mtgjson_allprintings(path: Path = MTGJSON_PATH) -> tuple[Path, bool]:
    if path.exists() and path.stat().st_size > 0:
        print(f"✅ Usando MTGJSON local: {path} ({path.stat().st_size/1024/1024:.1f} MB)")
        return path, False
    print(f"📥 MTGJSON AllPrintings no existe. Se descargará a {path}")
    download_to(path, MTGJSON_URL, label="Descargando AllPrintings (MTGJSON)")
    return path, True

def ensure_scryfall_default(path: Path = SCRY_DEFAULT_PATH) -> tuple[Path, bool]:
    if path.exists() and path.stat().st_size > 0:
        print(f"✅ Usando Scryfall cache: {path} ({path.stat().st_size/1024/1024:.1f} MB)")
        return path, False

    print("📥 Scryfall default_cards no existe. Se consultará bulk-data y se descargará.")
    bulk = requests.get(SCRYFALL_BULK_API, timeout=60).json()
    item = next(x for x in bulk["data"] if x["type"] == SCRYFALL_DEFAULT_TYPE)
    download_uri = item["download_uri"]
    download_to(path, download_uri, label="Descargando Scryfall default_cards")
    return path, True

def build_arena_scryfall_ids(scry_default_path: Path) -> set[str]:
    cards = load_json(scry_default_path)
    arena_ids: set[str] = set()
    for c in cards:
        games = c.get("games") or []
        lang = c.get("lang")
        if "arena" in games and lang == "en":
            sid = c.get("id")
            if sid:
                arena_ids.add(sid)
    print(f"✅ Printings con game:arena & lang=en en Scryfall: {len(arena_ids)}")
    return arena_ids

def build_legend() -> Dict[str, str]:
    return {
        "Legal": "L",
        "Banned": "B",
        "Restricted": "R",
        "Not Legal": "NL",
    }

def compact_legalities_dict(legalities: Dict[str, str] | None, legend: Dict[str, str]) -> Dict[str, str]:
    if not legalities:
        return {}
    comp: Dict[str, str] = {}
    for fmt in KEEP_FORMATS:
        st = legalities.get(fmt)
        if not st:
            continue
        code = legend.get(st)
        if not code:
            continue
        comp[fmt] = code
    return comp

def encode_legalities_as_string(comp_legs: Dict[str, str]) -> str:
    if not comp_legs:
        return ",".join("." for _ in KEEP_FORMATS)
    return ",".join(comp_legs.get(fmt, ".") for fmt in KEEP_FORMATS)

def build_ultra_from_allprintings(mtg_path: Path, arena_ids: Set[str]) -> Dict[str, Any]:
    print(f"[ULTRA] Cargando AllPrintings desde {mtg_path}...")
    payload = load_json(mtg_path)
    meta = payload.get("meta", {})
    sets = payload.get("data", {})

    legend = build_legend()
    ultra_sets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    count_cards = 0

    for set_code, set_obj in sets.items():
        cards = set_obj.get("cards", [])
        for card in cards:
            if card.get("language") != "English":
                continue

            identifiers = card.get("identifiers") or {}
            scry_id = identifiers.get("scryfallId")
            if not scry_id:
                continue

            if scry_id not in arena_ids:
                continue

            comp_legs = compact_legalities_dict(card.get("legalities"), legend)
            code = encode_legalities_as_string(comp_legs)

            if all(ch == "." for ch in code.split(",")):
                continue

            text = card.get("text") or ""
            ultra_card = {
                "id": scry_id,
                "n": card.get("name"),
                "s": set_code,
                "l": code,
                "t": text,
            }
            ultra_sets[set_code].append(ultra_card)
            count_cards += 1

    ultra = {
        "m": {
            "date": meta.get("date"),
            "version": meta.get("version"),
        },
        "fm": KEEP_FORMATS,
        "ll": legend,
        "d": ultra_sets,
        "f": {"createdAt": utc_now_z(), "sets": len(ultra_sets), "cards": count_cards},
    }

    print(f"[ULTRA] sets: {len(ultra_sets)}, cartas: {count_cards}")
    return ultra

# ---------------- FRESCURA DE ULTRA ----------------
def ultra_is_fresh(path: Path, max_age_days: int = 31) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False

    try:
        obj = load_json(path)
    except Exception:
        return False

    meta = obj.get("m", {})
    date_str = meta.get("date")
    if not date_str:
        return False

    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return False

    age = datetime.now(UTC) - dt
    return age <= timedelta(days=max_age_days)

# ---------------- SCRYFALL POR FORMATO ----------------
def fetch_format_from_scryfall(fmt: str) -> List[Dict[str, Any]]:
    all_cards: List[Dict[str, Any]] = []
    q = f"format:{fmt} game:arena lang:en"
    params = {
        "q": q,
        "unique": "prints",
        "order": "set",
        "dir": "asc",
        "include_extras": "false",
        "include_multilingual": "false",
    }

    url = SCRYFALL_SEARCH_URL
    print(f"[{fmt}] Consultando Scryfall: {q}")
    while url:
        print(f"[{fmt}] GET {url}")
        resp = requests.get(url, params=params if url == SCRYFALL_SEARCH_URL else None, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        cards = data.get("data", [])
        all_cards.extend(cards)
        print(f"  + {len(cards)} cartas (acumulado {len(all_cards)})")

        if data.get("has_more"):
            url = data.get("next_page")
            params = None
            time.sleep(0.2)
        else:
            url = None

    print(f"[{fmt}] Total {fmt}+Arena+en: {len(all_cards)}")
    return all_cards

def inject_missing_for_format(fmt: str, fmt_index: int, ultra: Dict[str, Any], fmt_cards: List[Dict[str, Any]]) -> None:
    legend = ultra["ll"]
    ultra_sets: Dict[str, List[Dict[str, Any]]] = ultra["d"]

    ultra_ids_fmt: Set[str] = set()
    for set_code, cards in ultra_sets.items():
        for c in cards:
            cid = c.get("id")
            if not cid:
                continue
            l_str = c.get("l") or ""
            parts = l_str.split(",") if l_str else []
            if len(parts) > fmt_index and parts[fmt_index] in ("L", "R"):
                ultra_ids_fmt.add(cid)

    scry_ids_fmt: Set[str] = set()
    by_id: Dict[str, Dict[str, Any]] = {}
    for card in fmt_cards:
        cid = card.get("id")
        if not cid:
            continue
        scry_ids_fmt.add(cid)
        by_id[cid] = card

    missing_ids = scry_ids_fmt - ultra_ids_fmt
    print(f"[Diff:{fmt}] Scryfall IDs : {len(scry_ids_fmt)}")
    print(f"[Diff:{fmt}] ULTRA IDs    : {len(ultra_ids_fmt)}")
    print(f"[Diff:{fmt}] Faltantes    : {len(missing_ids)}")

    added_per_set = Counter()
    for cid in missing_ids:
        card = by_id[cid]
        set_code = (card.get("set") or "").upper()
        if not set_code:
            continue

        comp_legs = compact_legalities_dict(card.get("legalities"), legend)
        code = encode_legalities_as_string(comp_legs)
        if all(ch == "." for ch in code.split(",")):
            continue

        text = card.get("oracle_text") or ""
        ultra_card = {
            "id": cid,
            "n": card.get("name"),
            "s": set_code,
            "l": code,
            "t": text,
        }
        ultra_sets[set_code].append(ultra_card)
        added_per_set[set_code] += 1

    if missing_ids:
        print(f"[Inject:{fmt}] Cartas agregadas a ULTRA por set:")
        for set_code, n in added_per_set.most_common():
            print(f"  {set_code}: {n}")
    else:
        print(f"[Inject:{fmt}] No había faltantes que inyectar.")

def generate_allprintings_ultra() -> None:
    # Si ULTRA existe y es más reciente que 31 días, no lo regeneramos
    if ultra_is_fresh(AP_OUT_JSON, max_age_days=31):
        print(f"✅ AllPrintings ULTRA está fresco (<31 días). Se omite regeneración.")
        return

    mtg_path, mtg_downloaded = ensure_mtgjson_allprintings()
    scry_path, scry_downloaded = ensure_scryfall_default()
    arena_ids = build_arena_scryfall_ids(scry_path)

    ultra = build_ultra_from_allprintings(mtg_path, arena_ids)
    dump_minified_json(AP_OUT_JSON, ultra)
    print(f"💾 AllPrintings ULTRA base: {AP_OUT_JSON} ({AP_OUT_JSON.stat().st_size/1024/1024:.2f} MB)")

    # Refuerzo solo para algunos formatos (el resto se queda con MTGJSON/base)
    for fmt in FORMATS_TO_REINFORCE:
        if fmt not in KEEP_FORMATS:
            continue
        fmt_index = KEEP_FORMATS.index(fmt)
        fmt_cards = fetch_format_from_scryfall(fmt)
        fmt_path = Path(f"arena_{fmt}.json")
        dump_minified_json(fmt_path, fmt_cards)
        print(f"[{fmt}] Guardado en {fmt_path}")
        inject_missing_for_format(fmt, fmt_index, ultra, fmt_cards)

    dump_minified_json(AP_OUT_JSON, ultra)
    print(f"✅ AllPrintings ULTRA final: {AP_OUT_JSON} ({AP_OUT_JSON.stat().st_size/1024/1024:.2f} MB)")

    if AP_WRITE_GZ:
        dump_minified_gzip_json(AP_OUT_GZ, ultra, compresslevel=9)
        print(f"✅ AllPrintings ULTRA gzip: {AP_OUT_GZ} ({AP_OUT_GZ.stat().st_size/1024/1024:.2f} MB)")

    if not KEEP_DOWNLOADED:
        if mtg_downloaded:
            MTGJSON_PATH.unlink(missing_ok=True)
            print(f"🧽 Eliminado descargado: {MTGJSON_PATH}")
        if scry_downloaded:
            SCRY_DEFAULT_PATH.unlink(missing_ok=True)
            print(f"🧽 Eliminado descargado: {SCRY_DEFAULT_PATH}")

# ---------------- COMPREHENSIVE RULES ----------------
def download_magiccomprules_txt(url: str, dest_folder: Path) -> Path:
    dest_folder.mkdir(parents=True, exist_ok=True)
    clean_url = url.split("?", 1)[0]
    filename = requests.utils.unquote(clean_url.split("/")[-1])
    out_path = dest_folder / filename
    with Dots(f"Descargando {filename}"):
        r = requests.get(clean_url, timeout=120)
        r.raise_for_status()
        out_path.write_bytes(r.content)
    return out_path

RE_HEADER = re.compile(r"^\d+\.\s+\S")
RE_RULE = re.compile(r"^(?P<id>\d+\.\d+(?:\.\d+)?[a-z]?)\.?\s+(?P<body>.+)$")

def parse_rules(txt: str) -> list[dict]:
    lines = txt.replace("\r\n", "\n").split("\n")
    rules: list[dict] = []
    header_stack: list[str] = []
    current: dict | None = None

    def push_current():
        nonlocal current
        if not current:
            return
        current["text"] = current["text"].strip()
        rules.append(current)
        current = None

    for raw in lines:
        s = raw.strip()
        if not s:
            continue

        if RE_HEADER.match(s) and not re.match(r"^\d+\.\d", s):
            header_stack.append(s)
            if len(header_stack) > 8:
                header_stack.pop(0)
            continue

        m = RE_RULE.match(s)
        if m:
            push_current()
            current = {"id": m.group("id"), "path": list(header_stack), "text": m.group("body")}
            continue

        if current is not None:
            current["text"] += "\n" + s

    push_current()
    return rules

def apply_tokens(s: str) -> str:
    for k in sorted(TOKENS.keys(), key=len, reverse=True):
        s = s.replace(k, TOKENS[k])
    return s

def build_ultra_flat(rules: list[dict], tokenized: bool = True) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in rules:
        rid = r["id"]
        txt = r["text"]
        if tokenized:
            txt = apply_tokens(txt)
        txt = " ".join(txt.split())
        out[rid] = txt
    return out

def write_control_manifest(
    input_path: Path,
    outputs: list[Path],
    source_url: str,
    generated_at: str,
    token_map: dict[str, str],
) -> Path:
    manifest = {
        "metadata": {
            "generatedAt": generated_at,
            "script": str(Path(__file__).resolve()),
            "cwd": str(Path.cwd().resolve()),
        },
        "source": {
            "url": source_url,
            "file": build_file_info(input_path),
        },
        "outputs": [build_file_info(p) for p in outputs],
        "tokenLegend": {v: k for k, v in token_map.items()},
    }
    dump_pretty_json(OUT_CONTROL_JSON, manifest)
    return OUT_CONTROL_JSON

# ---------------- MAIN ----------------
def main():
    repo_root = Path.cwd().resolve()
    script_path = Path(__file__).resolve()
    print(f"repo_root     : {repo_root}")
    print(f"__file__      : {script_path}")
    print(f"generatedAt   : {utc_now_z()}")

    print("\n=== STEP 0: Consume New_brains ===")
    consume_new_brains(repo_root)

    print("\n=== STEP A: AllPrintings (MTGJSON + Scryfall) ===")
    generate_allprintings_ultra()

    print("\n=== STEP B: Comprehensive Rules ===")
    input_path = download_magiccomprules_txt(COMP_RULES_TXT_URL, repo_root)
    print(f"📄 Fuente: {input_path.name}")

    generated_at = utc_now_z()
    txt = input_path.read_text(encoding="utf-8", errors="replace")
    rules = parse_rules(txt)

    normal_obj = {
        "metadata": {
            "sourceUrl": COMP_RULES_TXT_URL,
            "sourceFile": str(input_path),
            "generatedAt": generated_at,
            "count": len(rules),
        },
        "rules": rules,
    }
    dump_pretty_json(OUT_NORMAL_JSON, normal_obj)
    print(f"✅ Normal: {OUT_NORMAL_JSON} ({OUT_NORMAL_JSON.stat().st_size/1024/1024:.2f} MB)")

    ultra_rules = build_ultra_flat(rules, tokenized=True)
    ultra_obj = {
        "metadata": {
            "sourceUrl": COMP_RULES_TXT_URL,
            "sourceFile": str(input_path),
            "generatedAt": generated_at,
            "count": len(ultra_rules),
            "format": "ultra-flat-v2",
            "tokenized": True,
        },
        "legend": {v: k for k, v in TOKENS.items()},
        "rules": ultra_rules,
    }
    dump_minified_json(OUT_ULTRA_FLAT_JSON, ultra_obj)
    print(f"✅ Ultra-flat: {OUT_ULTRA_FLAT_JSON} ({OUT_ULTRA_FLAT_JSON.stat().st_size/1024/1024:.2f} MB)")

    outputs = [
        AP_OUT_JSON,
        *( [AP_OUT_GZ] if AP_WRITE_GZ else [] ),
        OUT_NORMAL_JSON,
        OUT_ULTRA_FLAT_JSON,
    ]

    if CR_WRITE_GZ:
        dump_minified_gzip_json(OUT_ULTRA_FLAT_GZ, ultra_obj, compresslevel=9)
        print(f"✅ Ultra-flat gzip: {OUT_ULTRA_FLAT_GZ} ({OUT_ULTRA_FLAT_GZ.stat().st_size/1024/1024:.2f} MB)")
        outputs.append(OUT_ULTRA_FLAT_GZ)

    control_path = write_control_manifest(
        input_path=input_path,
        outputs=[p for p in outputs if p.exists()],
        source_url=COMP_RULES_TXT_URL,
        generated_at=generated_at,
        token_map=TOKENS,
    )
    print(f"✅ Control: {control_path} ({control_path.stat().st_size/1024/1024:.2f} MB)")

    print("\n=== DONE ===")
    print("Archivos generados (para commit/push por GitHub Actions):")
    for p in outputs + [control_path]:
        if p.exists():
            print(f"- {p} ({p.stat().st_size/1024/1024:.2f} MB)")
        else:
            print(f"- {p} (NO EXISTE)")


if __name__ == "__main__":
    main()

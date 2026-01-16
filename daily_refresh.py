# ============================================================
# VER        DATE            DETAIL
# 1.0        16-01-2026      SE AGREGA mtgaLegalSetsByFormat PARA MANEJO DE SETS POR FORMATO
# 1.1        16-01-2026      FIX - mtgaLegalSetsByFormat PARA MANEJO DE SETS POR FORMATO
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
from datetime import datetime, UTC

import requests

# ============================================================
# CI-FIRST SCRIPT (GitHub Actions friendly)
# - No menú, no tokens, no GitHub Contents API.
# - Genera AllPrintings (MTGJSON + Scryfall) y Comprehensive Rules.
# - Consume brains desde New_brains/:
#     * mueve a raíz
#     * archiva brain anterior en raíz con fecha
#     * deja marker vacío en New_brains: *.brain.json_ddmmyy_processed
# - El "push" lo hace el workflow vía git commit/push usando GITHUB_TOKEN.
#   (Requiere permissions: contents: write en el workflow)
# ============================================================

# ---------------- CONFIG ----------------
# 1) ALLPRINTINGS (MTGJSON + SCRYFALL)
MTGJSON_URL = "https://mtgjson.com/api/v5/AllPrintings.json"
SCRYFALL_BULK_API = "https://api.scryfall.com/bulk-data"
SCRYFALL_DEFAULT_TYPE = "default_cards"

MIN_PATH = Path("AllPrintings_MTGA_EN_MIN.json")
AP_OUT_JSON = Path("AllPrintings_MTGA_EN_ULTRA.json")
AP_OUT_GZ = Path("AllPrintings_MTGA_EN_ULTRA.json.gz")
AP_WRITE_GZ = True

MTGJSON_PATH = Path("AllPrintings.json")
SCRY_CACHE_DIR = Path("cache_scryfall")
SCRY_DEFAULT_PATH = SCRY_CACHE_DIR / "default_cards.json"

# En CI, ideal: KEEP_DOWNLOADED=True + actions/cache para reusar descargas.
# Si no cacheas, puedes dejarlo en False para no dejar basura.
KEEP_DOWNLOADED = True

KEEP_FORMATS = {"standard", "alchemy", "explorer", "historic", "timeless", "brawl"}
FORMATS_ORDER = ["standard", "alchemy", "explorer", "historic", "timeless", "brawl"]

# MAPA ESTÁTICO DE SETS LEGALES POR FORMATO (ENERO 2026)
MTGA_LEGAL_SETS_BY_FORMAT = {
    "standard": ["MKM", "OTJ", "LCI", "MOM", "ONE", "VOC", "BLB", "DFT", "AED"],
    "explorer": [
        "ZNR", "KHM", "STX", "AFR", "MID", "VOW", "NEO", "SNC",
        "DMU", "BRO", "ONE", "MOM", "LCI", "MKM", "OTJ", "VOC", "BLB", "DFT", "AED",
    ],
    "historic": [
        "XLN", "RIX", "DOM", "M19", "GRN", "RNA", "WAR", "ELD",
        "THB", "IKO", "M21", "ZNR", "KHM", "STX", "AFR", "MID",
        "VOW", "NEO", "SNC", "DMU", "BRO", "ONE", "MOM", "LCI",
        "MKM", "OTJ", "VOC", "BLB", "DFT", "AED",
    ],
    "timeless": [
        "XLN", "RIX", "DOM", "M19", "GRN", "RNA", "WAR", "ELD",
        "THB", "IKO", "M21", "ZNR", "KHM", "STX", "AFR", "MID",
        "VOW", "NEO", "SNC", "DMU", "BRO", "ONE", "MOM", "LCI",
        "MKM", "OTJ", "VOC", "BLB", "DFT", "AED",
    ],
    "alchemy": [],
    "brawl": [],
}

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

def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def dump_pretty_json(path: Path, obj: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def dump_minified_json(path: Path, obj: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))

def dump_minified_gzip_json(path: Path, obj: dict, compresslevel: int = 9) -> None:
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

# ---------------- BRAINS: consume from New_brains ----------------
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
    """
    Regla:
    - Si existe New_brains/{gemini|grooq}.brain.json:
        1) Si en raíz existe {file}, archivarlo con _ddmmyyyy (y _2, _3 si colisiona)
        2) Mover New_brains/{file} -> raíz/{file}
        3) Crear marker vacío en New_brains: {file}_ddmmyy_processed (y _2, _3 si colisiona)
    """
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

        # 1) Archivar brain existente en raíz (si existe)
        if dst.exists():
            archived = _unique_path(repo_root / f"{fname}_{date_full}")
            dst.rename(archived)
            print(f"🗄️ Archivado root: {dst.name} -> {archived.name}")

        # 2) Mover nuevo brain a raíz
        src.rename(dst)
        print(f"📦 Movido: {src} -> {dst}")
        moved_any = True

        # 3) Crear marker vacío en New_brains
        marker = _unique_path(new_dir / f"{fname}_{date_short}_processed")
        marker.write_text("", encoding="utf-8")
        print(f"🏷️ Marker vacío: {marker}")

    if not moved_any:
        print("ℹ️ No se encontraron brains nuevos para mover.")

# ---------------- ALLPRINTINGS: DOWNLOAD + MIN + ULTRA ----------------
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
        if "arena" in games:
            sid = c.get("id")
            if sid:
                arena_ids.add(sid)
    print(f"✅ Printings con Arena en Scryfall: {len(arena_ids)}")
    return arena_ids

SEED_CODES = {"Legal": "L", "Banned": "B", "Restricted": "R", "Not Legal": "NL"}

def _abbr_candidates(status: str) -> list[str]:
    words = [w for w in status.replace("-", " ").split() if w]
    initials = "".join(w[0].upper() for w in words) if words else status[:1].upper()
    compact = "".join(ch for ch in status.upper() if ch.isalnum())

    cands = []
    if initials:
        cands.append(initials)
        for i in range(1, len(initials) + 1):
            cands.append(initials[:i])
    for i in range(1, min(len(compact), 6) + 1):
        cands.append(compact[:i])

    out, seen = [], set()
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out

def build_legality_legend(all_statuses: set[str]) -> dict[str, str]:
    code_to_status = {code: status for status, code in SEED_CODES.items()}
    status_to_code = dict(SEED_CODES)

    for status in sorted(all_statuses):
        if status in status_to_code:
            continue
        for cand in _abbr_candidates(status):
            if cand not in code_to_status:
                status_to_code[status] = cand
                code_to_status[cand] = status
                break
        else:
            base = _abbr_candidates(status)[0] if status else "U"
            n = 2
            while f"{base}{n}" in code_to_status:
                n += 1
            code = f"{base}{n}"
            status_to_code[status] = code
            code_to_status[code] = status
    return status_to_code

def compact_legalities_dict(legalities: dict | None, legend: dict[str, str]) -> dict:
    if not isinstance(legalities, dict):
        return {}
    out = {}
    for fmt in KEEP_FORMATS:
        v = legalities.get(fmt)
        if v:
            out[fmt] = legend.get(v, v)
    return out

def generate_min_if_missing() -> None:
    if MIN_PATH.exists() and MIN_PATH.stat().st_size > 0:
        print(f"✅ MIN existe: {MIN_PATH} ({MIN_PATH.stat().st_size/1024/1024:.2f} MB)")
        return

    print(f"⚠️ MIN no existe ({MIN_PATH}). Se generará desde MTGJSON + Scryfall.")
    mtg_path, mtg_downloaded = ensure_mtgjson_allprintings()
    scry_path, scry_downloaded = ensure_scryfall_default()
    arena_ids = build_arena_scryfall_ids(scry_path)
    payload = load_json(mtg_path)

    statuses: set[str] = set(SEED_CODES.keys())
    scanned = 0
    for set_obj in payload["data"].values():
        for card in set_obj.get("cards", []):
            scanned += 1
            if card.get("language") != "English":
                continue
            identifiers = card.get("identifiers") or {}
            scry_id = identifiers.get("scryfallId")
            if not scry_id or scry_id not in arena_ids:
                continue
            legalities = card.get("legalities")
            if isinstance(legalities, dict):
                for fmt in KEEP_FORMATS:
                    v = legalities.get(fmt)
                    if v:
                        statuses.add(v)

    legend = build_legality_legend(statuses)

    # ✂️ NUEVO: Determinar qué sets son relevantes para ALGÚN formato que mantenemos
    relevant_sets = set()
    for fmt in KEEP_FORMATS:
        if fmt in MTGA_LEGAL_SETS_BY_FORMAT:
            relevant_sets.update(MTGA_LEGAL_SETS_BY_FORMAT[fmt])

    print(f"✅ Sets relevantes para {KEEP_FORMATS}: {len(relevant_sets)} sets")

    out_data: dict[str, list[dict]] = {}
    kept_cards = 0
    kept_sets = 0

    for set_code, set_obj in payload["data"].items():
        # ✂️ SOLO procesar sets que están en al menos un formato relevante
        if set_code not in relevant_sets:
            continue

        new_cards = []
        for card in set_obj.get("cards", []):
            if card.get("language") != "English":
                continue
            identifiers = card.get("identifiers") or {}
            scry_id = identifiers.get("scryfallId")
            if not scry_id or scry_id not in arena_ids:
                continue
            new_cards.append({
                "name": card.get("name"),
                "set": card.get("setCode", set_code),
                "legalities": compact_legalities_dict(card.get("legalities"), legend),
                "text": card.get("text"),
            })
            kept_cards += 1
        if new_cards:
            out_data[set_code] = new_cards
            kept_sets += 1

    out_payload = {
        "meta": payload.get("meta", {}),
        "legalityLegend": legend,
        "data": out_data,
        "filtered": {
            "createdAt": utc_now_z(),
            "scannedCards": scanned,
            "keptCards": kept_cards,
            "keptSets": kept_sets,
            "keptFormats": sorted(KEEP_FORMATS),
            "minified": True,
        }
    }

    print(f"💾 Escribiendo MIN: {MIN_PATH}")
    dump_minified_json(MIN_PATH, out_payload)
    print(f"✅ MIN listo: {MIN_PATH} ({MIN_PATH.stat().st_size/1024/1024:.2f} MB)")

    if not KEEP_DOWNLOADED:
        if mtg_downloaded:
            MTGJSON_PATH.unlink(missing_ok=True)
            print(f"🧽 Eliminado descargado: {MTGJSON_PATH}")
        if scry_downloaded:
            SCRY_DEFAULT_PATH.unlink(missing_ok=True)
            print(f"🧽 Eliminado descargado: {SCRY_DEFAULT_PATH}")

def encode_legalities_as_string(leg_dict: dict | None) -> str:
    if not isinstance(leg_dict, dict) or not leg_dict:
        return ",".join(["."] * len(FORMATS_ORDER))
    return ",".join([(leg_dict.get(fmt) or ".") for fmt in FORMATS_ORDER])

def build_ultra_from_min(min_obj: dict) -> dict:
    meta = min_obj.get("meta", {})
    legend = min_obj.get("legalityLegend", {})
    data = min_obj.get("data", {})

    out_d = {}
    cards = 0
    sets = 0

    for set_code, arr in data.items():
        if not arr:
            continue
        new_arr = []
        for c in arr:
            new_arr.append({
                "n": c.get("name"),
                "l": encode_legalities_as_string(c.get("legalities")),
                "t": c.get("text"),
            })
            cards += 1
        out_d[set_code] = new_arr
        sets += 1

    return {
        "m": meta,
        "ll": legend,
        "fm": FORMATS_ORDER,
        "d": out_d,
        "f": {"createdAt": utc_now_z(), "sets": sets, "cards": cards},
        "mtgaLegalSetsByFormat": MTGA_LEGAL_SETS_BY_FORMAT,
    }

def generate_allprintings_ultra() -> None:
    generate_min_if_missing()
    min_obj = load_json(MIN_PATH)
    ultra = build_ultra_from_min(min_obj)

    print(f"💾 Escribiendo AllPrintings ULTRA: {AP_OUT_JSON}")
    dump_minified_json(AP_OUT_JSON, ultra)
    print(f"✅ AllPrintings ULTRA listo: {AP_OUT_JSON} ({AP_OUT_JSON.stat().st_size/1024/1024:.2f} MB)")

    if AP_WRITE_GZ:
        print(f"💾 Escribiendo AllPrintings ULTRA gzip: {AP_OUT_GZ}")
        dump_minified_gzip_json(AP_OUT_GZ, ultra, compresslevel=9)
        print(f"✅ AllPrintings ULTRA gzip listo: {AP_OUT_GZ} ({AP_OUT_GZ.stat().st_size/1024/1024:.2f} MB)")

# ---------------- COMPREHENSIVE RULES: DOWNLOAD + PARSE ----------------
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

    # 0) Consume brains desde New_brains (si existen)
    print("\n=== STEP 0: Consume New_brains ===")
    consume_new_brains(repo_root)

    # A) AllPrintings primero
    print("\n=== STEP A: AllPrintings (MTGJSON + Scryfall) ===")
    generate_allprintings_ultra()

    # B) Comprehensive Rules
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
        MIN_PATH,
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

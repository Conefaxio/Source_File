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

# Constants
MTGJSON_URL = "https://mtgjson.com/api/v5/AllPrintings.json"
SCRYFALL_BULK_API = "https://api.scryfall.com/bulk-data"
SCRYFALL_DEFAULT_TYPE = "default_cards"
MIN_PATH = Path("AllPrintings_MTGA_EN_MIN.json")
AP_OUT_JSON = Path("AllPrintings_MTGA_EN_ULTRA.json")
AP_OUT_GZ = Path("AllPrintings_MTGA_EN_ULTRA.json.gz")
AP_WRITE_GZ = True
MTGJSON_PATH = Path("AllPrintings.json")
SCRY_CACHE_DIR = Path("cache/scryfall")
SCRY_DEFAULT_PATH = SCRY_CACHE_DIR / "default_cards.json"

KEEP_DOWNLOADED = True
KEEP_FORMATS = ["standard", "alchemy", "explorer", "historic", "timeless", "brawl"]
FORMATS_ORDER = ["standard", "alchemy", "explorer", "historic", "timeless", "brawl"]

TITLE_1 = "1. ALL-PRINTINGS (MTGJSON + SCRYFALL)"

COMPRULES_TXT_URL = "https://media.wizards.com/2025/downloads/MagicCompRules%20202501114.txt"
CR_WRITE_GZ = True
OUT_NORMAL_JSON = Path("MagicCompRules.parsed.json")
OUT_ULTRA_FLAT_JSON = Path("MagicCompRules.ultraflat.json")
OUT_ULTRA_FLAT_GZ = Path("MagicCompRules.ultraflat.json.gz")
OUT_CONTROL_JSON = Path("MagicCompRules.control.json")

TOKENS = {
    "This is a state-based action.": "SBA",
    "See rule": "SR",
    "in turn order": "ITO",
    "the active player": "AP",
    "nonactive player": "NAP",
}

TITLE_2 = "2. COMPREHENSIVE RULES"

BRAIN_FILES = ["gemini.brain.json", "grooq.brain.json"]
NEW_BRAINS_DIR = Path("Newbrains")

TITLE_3 = "3. BRAINS"

# ---------------- UTILS ----------------

def utcnowz() -> str:
    return datetime.now(UTC).isoformat(timespec='seconds').replace('+00:00', 'Z')

def load_json(path: Path) -> dict:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def dump_pretty_json(path: Path, obj: dict) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def dump_minified_json(path: Path, obj: dict) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))

def dump_minified_gzip_json(path: Path, obj: dict, compresslevel: int = 9) -> None:
    with gzip.open(path, 'wt', encoding='utf-8', compresslevel=compresslevel) as f:
        json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))

def file_sha256(path: Path, chunksize: int = 65536) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(chunksize), b''):
            h.update(chunk)
    return h.hexdigest()

def file_linecount(path: Path) -> int:
    if path.suffix.lower() == '.gz':
        with gzip.open(path, 'rt', encoding='utf-8', errors='replace') as f:
            return sum(1 for _ in f)
    else:
        with open(path, 'rt', encoding='utf-8', errors='replace') as f:
            return sum(1 for _ in f)

def build_fileinfo(path: Path) -> dict:
    st = path.stat()
    return {
        'name': path.name,
        'path': str(path.resolve()),
        'sizeBytes': st.st_size,
        'sizeMB': round(st.st_size / 1024 / 1024, 4),
        'modifiedAtLocal': datetime.fromtimestamp(st.st_mtime).isoformat(timespec='seconds'),
        'sha256': file_sha256(path),
        'lineCount': file_linecount(path),
    }

# ---------------- 3 DOTS ANIMATION ----------------

class Dots:
    def __init__(self, label: str):
        self.label = label
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exctype, exc, tb):
        self.stop.set()
        self.thread.join(timeout=2)

    def run(self):
        i = 0
        while not self.stop.is_set():
            dots = '.' * (i % 4 + 1)
            sys.stdout.write(f'\r{self.label}{dots}')
            sys.stdout.flush()
            i += 1
            time.sleep(0.35)
        sys.stdout.write('\r' + ' ' * (len(self.label) + 6))
        sys.stdout.flush()

# ---------------- GENERIC DOWNLOAD (stream) ----------------

def download_to_path(path: Path, url: str, timeout: int = 1800, label: str = "Descargando") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.part')
    with Dots(label):
        with requests.get(url, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            with open(tmp, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
    tmp.replace(path)

# ---------------- BRAINS (consume from Newbrains) ----------------

def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    base = p.name
    i = 2
    while True:
        candidate = p.with_name(f"{base}{i}")
        if not candidate.exists():
            return candidate
        i += 1

def date_tag_full() -> str:
    return datetime.now(UTC).strftime("%d%m%Y")  # 07012026

def date_tag_short() -> str:
    return datetime.now(UTC).strftime("%d%m%y")  # 070126

def consume_new_brains(repo_root: Path) -> None:
    # Regla:
    # - Si existe Newbrains/gemini/grooq.brain.json
    # 1. Si en raiz existe file, archivarlo con ddmmyyyy y 2,3 si colisiona
    # 2. Mover Newbrains/file -> raiz/file
    # 3. Crear marker vacio en Newbrains file+ddmmyy+processed y 2,3 si colisiona
    new_dir = repo_root / NEW_BRAINS_DIR
    if not new_dir.exists():
        print("Newbrains no existe. No hay brains para consumir.")
        return
    moved_any = False
    date_full = date_tag_full()
    date_short = date_tag_short()
    for fname in BRAIN_FILES:
        src = new_dir / fname
        if not src.exists():
            continue
        dst = repo_root / fname
        # TITLE 1. Archivar brain existente en raiz si existe...
        if dst.exists():
            archived = unique_path(repo_root / f"{fname}{date_full}")
            dst.rename(archived)
            print(f"Archivado raiz {dst.name} -> {archived.name}")
        # TITLE 2. Mover nuevo brain a raiz...
        src.rename(dst)
        print(f"Movido {src} -> {dst}")
        moved_any = True
        # TITLE 3. Crear marker vacio en Newbrains...
        marker = unique_path(new_dir / f"{fname}{date_short}processed")
        marker.write_text("", encoding='utf-8')
        print(f"Marker vacio {marker}")
    if not moved_any:
        print("No se encontraron brains nuevos para mover."

# ---------------- ALL-PRINTINGS DOWNLOAD (MIN + ULTRA) ----------------

def ensure_mtgjson_all_printings(path: Path = MTGJSON_PATH) -> tuple[Path, bool]:
    if path.exists() and path.stat().st_size > 0:
        print(f"Usando MTGJSON local {path} ({path.stat().st_size/1024/1024:.1f} MB)")
        return path, False
    print(f"MTGJSON AllPrintings no existe. Se descargar a {path}")
    download_to_path(path, MTGJSON_URL, label="Descargando AllPrintings MTGJSON")
    return path, True

def ensure_scryfall_default(path: Path = SCRY_DEFAULT_PATH) -> tuple[Path, bool]:
    if path.exists() and path.stat().st_size > 0:
        print(f"Usando Scryfall cache {path} ({path.stat().st_size/1024/1024:.1f} MB)")
        return path, False
    print("Scryfall default_cards no existe. Se consultar bulk-data y se descargar.")
    bulk = requests.get(SCRYFALL_BULK_API, timeout=60).json()
    item = next((x for x in bulk['data'] if x['type'] == SCRYFALL_DEFAULT_TYPE), None)
    download_uri = item['download_uri']
    download_to_path(path, download_uri, label="Descargando Scryfall default_cards")
    return path, True

def build_arena_scryfall_ids(scry_default_path: Path) -> set[str]:
    cards = load_json(scry_default_path)
    arena_ids = set[str]()
    for c in cards:
        games = c.get('games') or []
        if 'mtgo' in games or 'mtgarena' in games:
            sid = c.get('id')
            if sid:
                arena_ids.add(sid)
    print(f"Printings con Arena en Scryfall: {len(arena_ids)}")
    return arena_ids

SEED_CODES = {
    "Legal": "L",
    "Banned": "B",
    "Restricted": "R",
    "Not Legal": "NL"
}

def abbr_candidates(status: str) -> list[str]:
    words = [w for w in status.replace('-', ' ').split() if w]
    if words:
        initials = ''.join(w[0].upper() for w in words)
    else:
        initials = status[:1].upper()
    compact = ''.join(ch for ch in status.upper() if ch.isalnum())
    cands = []
    if initials:
        cands.append(initials)
        for i in range(1, len(initials)):
            cands.append(initials[:i])
    for i in range(1, min(len(compact), 6)):
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
        for cand in abbr_candidates(status):
            if cand not in code_to_status:
                status_to_code[status] = cand
                code_to_status[cand] = status
                break
        else:
            base = abbr_candidates(status)[0] if status else "U"
            n = 2
            while f"{base}{n}" in code_to_status:
                n += 1
            code = f"{base}{n}"
            status_to_code[status] = code
            code_to_status[code] = status
    return status_to_code

def compact_legalities(dict_legalities: dict | None, legend: dict[str, str]) -> dict:
    if not isinstance(dict_legalities, dict):
        return {}
    out = {}
    for fmt in KEEP_FORMATS:
        v = dict_legalities.get(fmt)
        if v:
            out[fmt] = legend.get(v, v)
    return out

def generate_mini_if_missing() -> None:
    if MIN_PATH.exists() and MIN_PATH.stat().st_size > 0:
        print(f"MIN existe {MIN_PATH} ({MIN_PATH.stat().st_size/1024/1024:.2f} MB)")
        return
    print(f"MIN no existe {MIN_PATH}. Se generar desde MTGJSON + Scryfall.")
    mtg_path, mtg_downloaded = ensure_mtgjson_all_printings()
    scry_path, scry_downloaded = ensure_scryfall_default()
    arena_ids = build_arena_scryfall_ids(scry_path)
    payload = load_json(mtg_path)
    # Scan legalities
    statuses = set(SEED_CODES.keys())
    scanned = 0
    for set_obj in payload['data'].values():
        for card in set_obj.get('cards', []):
            scanned += 1
            if card.get('language') != "en":
                continue
            identifiers = card.get('identifiers') or {}
            scry_id = identifiers.get('scryfallId')
            if not scry_id or scry_id not in arena_ids:
                continue
            legalities = card.get('legalities')
            if isinstance(legalities, dict):
                for fmt in KEEP_FORMATS:
                    v = legalities.get(fmt)
                    if v:
                        statuses.add(v)
    legend = build_legality_legend(statuses)
    # Build filtered data
    out_data = dict[str, list[dict]]
    kept_cards = 0
    kept_sets = 0
    for set_code, set_obj in payload['data'].items():
        new_cards = []
        for card in set_obj.get('cards', []):
            if card.get('language') != "en":
                continue
            identifiers = card.get('identifiers') or {}
            scry_id = identifiers.get('scryfallId')
            if not scry_id or scry_id not in arena_ids:
                continue
            new_cards.append({
                'name': card.get('name'),
                'set': card.get('setCode', set_code),
                'legalities': compact_legalities(card.get('legalities'), legend),
                'text': card.get('text'),
            })
            kept_cards += 1
        if new_cards:
            out_data[set_code] = new_cards
            kept_sets += 1
    out_payload = {
        **payload.get('meta', {}),
        'legalityLegend': legend,
        'data': out_data,
        'filtered': {
            'createdAt': utcnowz(),
            'scannedCards': scanned,
            'keptCards': kept_cards,
            'keptSets': kept_sets,
            'keptFormats': sorted(KEEP_FORMATS),
            'minified': True,
        },
    }
    print(f"Escribiendo MIN {MIN_PATH}")
    dump_minified_json(MIN_PATH, out_payload)
    print(f"MIN listo {MIN_PATH} ({MIN_PATH.stat().st_size/1024/1024:.2f} MB)")
    if not KEEP_DOWNLOADED:
        if mtg_downloaded:
            MTGJSON_PATH.unlink(missing_ok=True)
            print("Eliminado descargado MTGJSON_PATH")
        if scry_downloaded:
            SCRY_DEFAULT_PATH.unlink(missing_ok=True)
            print("Eliminado descargado SCRY_DEFAULT_PATH")

def encode_legalities_as_string(leg_dict: dict | None) -> str:
    if not isinstance(leg_dict, dict) or not leg_dict:
        return ''
    return ','.join(leg_dict.get(fmt) or '' for fmt in FORMATS_ORDER)

def build_set_code_map(payload: dict) -> dict[str, dict]:
    set_code_map = {}
    for set_code, set_obj in payload['data'].items():
        arena_id = set_obj.get('arenaId')
        if arena_id is not None:
            set_code_map[set_code] = {
                'name': set_obj.get('name'),
                'arenaId': arena_id,
                'releaseDate': set_obj.get('releaseDate'),
                'block': set_obj.get('block'),
                'scryfallId': set_obj.get('scryfallId'),
            }
    return set_code_map

def build_mtga_legal_sets(payload: dict, legend: dict[str, str]) -> dict[str, list[str]]:
    format_sets = {fmt: set() for fmt in FORMATS_ORDER}
    for set_code, set_obj in payload['data'].items():
        for card in set_obj.get('cards', []):
            if card.get('language') != "en":
                continue
            legalities = card.get('legalities')
            if isinstance(legalities, dict):
                for fmt in KEEP_FORMATS:
                    v = legalities.get(fmt)
                    if v and legend.get(v) == 'L':  # Assuming 'L' means Legal
                        format_sets[fmt].add(set_code)
                        break  # One legal card per set is enough
    mtga_legal_sets = {fmt: sorted(list(sets)) for fmt, sets in format_sets.items()}
    return mtga_legal_sets

def build_ultra_from_min(min_obj: dict, payload: dict) -> dict:
    meta = min_obj.get('meta', {})
    legend = min_obj.get('legalityLegend', {})
    data = min_obj.get('data', {})
    out_d = {}
    cards = 0
    sets = 0
    for set_code, arr in data.items():
        if not arr:
            continue
        new_arr = []
        for c in arr:
            new_arr.append({
                'n': c.get('name'),
                'l': encode_legalities_as_string(c.get('legalities')),
                't': c.get('text'),
            })
            cards += 1
        out_d[set_code] = new_arr
        sets += 1
    set_code_map = build_set_code_map(payload)
    mtga_legal_sets = build_mtga_legal_sets(payload, legend)
    return {
        **meta,
        'll': legend,
        'fm': FORMATS_ORDER,
        'd': out_d,
        'f': {
            'createdAt': utcnowz(),
            'sets': sets,
            'cards': cards,
        },
        'setCodeMap': set_code_map,
        'mtgaLegalSets': mtga_legal_sets,
    }

def generate_all_printings_ultra() -> None:
    generate_mini_if_missing()
    min_obj = load_json(MIN_PATH)
    payload = load_json(MTGJSON_PATH)  # Reload MTGJSON for set info
    ultra = build_ultra_from_min(min_obj, payload)
    print(f"Escribiendo AllPrintings ULTRA {AP_OUT_JSON}")
    dump_minified_json(AP_OUT_JSON, ultra)
    print(f"AllPrintings ULTRA listo {AP_OUT_JSON} ({AP_OUT_JSON.stat().st_size/1024/1024:.2f} MB)")
    if AP_WRITE_GZ:
        print(f"Escribiendo AllPrintings ULTRA gzip {AP_OUT_GZ}")
        dump_minified_gzip_json(AP_OUT_GZ, ultra, compresslevel=9)
        print(f"AllPrintings ULTRA gzip listo {AP_OUT_GZ} ({AP_OUT_GZ.stat().st_size/1024/1024:.2f} MB)")

# ---------------- COMPREHENSIVE RULES (DOWNLOAD + PARSE) ----------------

def download_magic_comp_rules_txt(url: str, dest_folder: Path) -> Path:
    dest_folder.mkdir(parents=True, exist_ok=True)
    clean_url = url.split('?')[0]
    filename = requests.utils.unquote(clean_url.split('/')[-1])
    out_path = dest_folder / filename
    with Dots(f"Descargando {filename}"):
        r = requests.get(clean_url, timeout=120)
        r.raise_for_status()
        out_path.write_bytes(r.content)
    return out_path

RE_HEADER = re.compile(r'^[0-9A-Z]')
RE_RULE = re.compile(r'(?P<id>\d+(?:\.\d+)*)(?:[a-z]?\.)?(?P<body>.*)')

def parse_rules_txt(txt: str) -> list[dict]:
    lines = txt.replace('\r', '').split('\n')
    rules: list[dict] = []
    header_stack: list[str] = []
    current: dict | None = None

    def push_current():
        nonlocal current
        if not current:
            return
        current['text'] = current['text'].strip()
        rules.append(current)
        current = None

    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        if RE_HEADER.match(s) and not re.match(r'^\d+\.\d+\.', s):
            header_stack.append(s)
            if len(header_stack) > 8:
                header_stack.pop(0)
            continue
        m = RE_RULE.match(s)
        if m:
            push_current()
            current = {
                'id': m.group('id'),
                'path': list(header_stack),
                'text': m.group('body'),
            }
            continue
        if current is not None:
            current['text'] += ' ' + s
    push_current()
    return rules

def apply_tokens(s: str) -> str:
    for k in sorted(TOKENS.keys(), key=len, reverse=True):
        s = s.replace(k, TOKENS[k])
    return s

def build_ultra_flat_rules(rules: list[dict], tokenized: bool = True) -> dict[str, str]:
    out = dict[str, str]()
    for r in rules:
        rid = r['id']
        txt = r['text']
        if tokenized:
            txt = apply_tokens(txt)
        txt = ' '.join(txt.split())
        out[rid] = txt
    return out

def write_control_manifest(input_path: Path, outputs: list[Path], source_url: str, generated_at: str, token_map: dict[str, str]) -> Path:
    manifest = {
        'metadata': {
            'generatedAt': generated_at,
            'script': str(Path(__file__).resolve()),
            'cwd': str(Path.cwd().resolve()),
        },
        'source': {
            'url': source_url,
            'file': build_fileinfo(input_path),
        },
        'outputs': [build_fileinfo(p) for p in outputs],
        'tokenLegend': {v: k for k, v in token_map.items()},
    }
    dump_pretty_json(OUT_CONTROL_JSON, manifest)
    return OUT_CONTROL_JSON

# ---------------- MAIN ----------------

def main():
    repo_root = Path.cwd().resolve()
    script_path = Path(__file__).resolve()
    print(f"{repo_root=}")
    print(f"{__file__} {script_path=}")
    print(f"generatedAt: {utcnowz()}")

    print("STEP 0. Consume Newbrains")
    consume_new_brains(repo_root)  # TITLE 0. Consume brains desde Newbrains si existen...

    print("STEP A. AllPrintings (MTGJSON + Scryfall)")
    generate_all_printings_ultra()  # TITLE A. AllPrintings primero...

    print("STEP B. Comprehensive Rules")
    input_path = download_magic_comp_rules_txt(COMPRULES_TXT_URL, repo_root)
    print(f"Fuente {input_path.name} {generated_at=}")
    generated_at = utcnowz()
    txt = input_path.read_text(encoding='utf-8', errors='replace')
    rules = parse_rules_txt(txt)

    normal_obj = {
        'metadata': {
            'sourceUrl': COMPRULES_TXT_URL,
            'sourceFile': str(input_path),
            'generatedAt': generated_at,
            'count': len(rules),
        },
        'rules': rules,
    }
    dump_pretty_json(OUT_NORMAL_JSON, normal_obj)
    print(f"Normal {OUT_NORMAL_JSON} ({OUT_NORMAL_JSON.stat().st_size/1024/1024:.2f} MB)")

    ultra_rules = build_ultra_flat_rules(rules, tokenized=True)
    ultra_obj = {
        'metadata': {
            'sourceUrl': COMPRULES_TXT_URL,
            'sourceFile': str(input_path),
            'generatedAt': generated_at,
            'count': len(ultra_rules),
            'format': 'ultra-flat-v2',
            'tokenized': True,
        },
        'legend': {v: k for k, v in TOKENS.items()},
        'rules': ultra_rules,
    }
    dump_minified_json(OUT_ULTRA_FLAT_JSON, ultra_obj)
    print(f"Ultra-flat {OUT_ULTRA_FLAT_JSON} ({OUT_ULTRA_FLAT_JSON.stat().st_size/1024/1024:.2f} MB)")

    outputs = [
        MIN_PATH,
        AP_OUT_JSON,
    ]
    if AP_WRITE_GZ:
        outputs.append(AP_OUT_GZ)
    outputs += [
        OUT_NORMAL_JSON,
        OUT_ULTRA_FLAT_JSON,
    ]
    if CR_WRITE_GZ:
        dump_minified_gzip_json(OUT_ULTRA_FLAT_GZ, ultra_obj, compresslevel=9)
        print(f"Ultra-flat gzip {OUT_ULTRA_FLAT_GZ} ({OUT_ULTRA_FLAT_GZ.stat().st_size/1024/1024:.2f} MB)")
        outputs.append(OUT_ULTRA_FLAT_GZ)

    # TITLE B. Comprehensive Rules...
    control_path = write_control_manifest(
        input_path=input_path,
        outputs=[p for p in outputs if p.exists()],
        source_url=COMPRULES_TXT_URL,
        generated_at=generated_at,
        token_map=TOKENS,
    )
    print(f"Control {control_path} ({control_path.stat().st_size/1024/1024:.2f} MB)")

    print("DONE!")
    print("Archivos generados para commit/push por GitHub Actions:")
    for p in outputs + [control_path]:
        if p.exists():
            print(f"- {p} ({p.stat().st_size/1024/1024:.2f} MB)")
        else:
            print(f"- {p} NO EXISTE")

if __name__ == '__main__':
    main()

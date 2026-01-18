#!/usr/bin/env python3
"""
meta_weekly_refresh.py
version: 1.0.0
date: 18-JAN-2026

Genera archivos Meta/<format>/decks.json e Meta/<format>/index.json
para los formatos: Standard, Alchemy, Explorer, Historic, Timeless.

Estructura:
  Source_File/
    Meta/
      standard/decks.json, index.json
      alchemy/decks.json, index.json
      historic/decks.json, index.json
      brawl/ (no se toca)
      timeless/decks.json, index.json
"""

import json
from datetime import datetime
from collections import defaultdict
import os

VERSION = "1.0.0"

# Config de formatos y carpeta destino dentro de Meta/
FORMATS = {
    "Standard": {"meta_key": "standard"},
    "Alchemy": {"meta_key": "alchemy"},
    "Explorer": {"meta_key": "explorer"},
    "Historic": {"meta_key": "historic"},
    "Timeless": {"meta_key": "timeless"},
}

# Datos de ejemplo por formato (puedes ampliarlos cuando quieras)
FORMAT_DATA = {
    "Standard": [
        {"archetype": "Izzet", "deck_id": "izzet-1384100", "matches": 2394, "set": "LCI"},
        {"archetype": "Mono White", "deck_id": "mono-white-1381262", "matches": 1654, "set": "LCI"},
        {"archetype": "Mono Green", "deck_id": "mono-green-1381859", "matches": 1125, "set": "OTJ"},
        {"archetype": "Azorius", "deck_id": "jeskai-1381787", "matches": 1461, "set": "LCI"},
        {"archetype": "Mono Green", "deck_id": "mono-green-1382962", "matches": 1241, "set": "OTJ"},
    ],
    "Alchemy": [
        {"archetype": "Izzet Control", "deck_id": "izzet-alc-001", "matches": 1856, "set": "ONE"},
        {"archetype": "Mono Red Aggro", "deck_id": "mono-red-alc-001", "matches": 1432, "set": "ONE"},
        {"archetype": "Selesnya Tokens", "deck_id": "selesnya-alc-001", "matches": 987, "set": "SIR"},
        {"archetype": "Grixis Midrange", "deck_id": "grixis-alc-001", "matches": 876, "set": "ONE"},
        {"archetype": "Orzhov Aggro", "deck_id": "orzhov-alc-001", "matches": 654, "set": "SIR"},
    ],
    "Explorer": [
        {"archetype": "Murktide Midrange", "deck_id": "murktide-exp-001", "matches": 2156, "set": "MH2"},
        {"archetype": "Living End", "deck_id": "living-exp-001", "matches": 1678, "set": "MH2"},
        {"archetype": "Rhinos", "deck_id": "rhinos-exp-001", "matches": 1423, "set": "MH2"},
        {"archetype": "Hammer Time", "deck_id": "hammer-exp-001", "matches": 987, "set": "MH2"},
        {"archetype": "Temur Murktide", "deck_id": "temur-exp-001", "matches": 765, "set": "MH2"},
    ],
    "Historic": [
        {"archetype": "Rakdos Midrange", "deck_id": "rakdos-his-001", "matches": 2234, "set": "DOM"},
        {"archetype": "Scam", "deck_id": "scam-his-001", "matches": 1876, "set": "DOM"},
        {"archetype": "Yawg Will", "deck_id": "yawg-his-001", "matches": 1543, "set": "DOM"},
        {"archetype": "Grindbrand", "deck_id": "grindbrand-his-001", "matches": 1234, "set": "DOM"},
        {"archetype": "Mystic Gate", "deck_id": "gate-his-001", "matches": 987, "set": "DOM"},
    ],
    "Timeless": [
        {"archetype": "Coco Combo", "deck_id": "coco-tim-001", "matches": 3421, "set": "ORI"},
        {"archetype": "Jund Cascade", "deck_id": "jund-tim-001", "matches": 2876, "set": "ORI"},
        {"archetype": "Murktide Control", "deck_id": "murk-tim-001", "matches": 2134, "set": "ORI"},
        {"archetype": "Storm", "deck_id": "storm-tim-001", "matches": 1876, "set": "ORI"},
        {"archetype": "Doomsday", "deck_id": "doom-tim-001", "matches": 1543, "set": "ORI"},
    ],
}


def build_decks_json(format_name: str, decks_data):
    """Construye Meta/<format>/decks.json simplificado."""
    decks_clean = [
        {
            "archetype": d["archetype"],
            "deck_id": d["deck_id"],
            "matches": d["matches"],
        }
        for d in decks_data
    ]
    return {
        "version": VERSION,
        "format": f"{format_name} BO1",
        "period": "Last 30 days",
        "generated": datetime.now().isoformat(),
        "total_decks": len(decks_clean),
        "decks": decks_clean,
    }


def build_index_json(format_name: str, decks_data):
    """Construye Meta/<format>/index.json con byArchetype/bySet."""
    by_archetype = defaultdict(list)
    by_set = defaultdict(list)

    for d in decks_data:
        archetype = d["archetype"]
        set_code = d["set"]

        by_archetype[archetype].append(
            {"deck_id": d["deck_id"], "matches": d["matches"], "set": set_code}
        )
        by_set[set_code].append(
            {"archetype": archetype, "deck_id": d["deck_id"], "matches": d["matches"]}
        )

    return {
        "version": VERSION,
        "format": f"{format_name} BO1",
        "period": "Last 30 days",
        "generated": datetime.now().isoformat(),
        "byArchetype": dict(by_archetype),
        "byCard": {},
        "byCommander": {},
        "bySet": dict(by_set),
    }


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def main():
    print("MTG Arena Multi-Format Meta Refresh")
    print("=" * 50)

    base_meta_dir = os.path.join("Source_File", "Meta")
    ensure_dir(base_meta_dir)

    for format_name, cfg in FORMATS.items():
        meta_key = cfg["meta_key"]
        decks_data = FORMAT_DATA.get(format_name, [])

        fmt_dir = os.path.join(base_meta_dir, meta_key)
        ensure_dir(fmt_dir)

        decks_json = build_decks_json(format_name, decks_data)
        index_json = build_index_json(format_name, decks_data)

        decks_path = os.path.join(fmt_dir, "decks.json")
        index_path = os.path.join(fmt_dir, "index.json")

        with open(decks_path, "w", encoding="utf-8") as f:
            json.dump(decks_json, f, indent=2, ensure_ascii=False)

        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index_json, f, indent=2, ensure_ascii=False)

        print(
            f"✓ {format_name}: {decks_path} & {index_path} "
            f"({len(decks_data)} decks, {len(index_json['byArchetype'])} archetypes)"
        )

    print("=" * 50)
    print("✓ META WEEKLY REFRESH COMPLETADO")


if __name__ == "__main__":
    main()

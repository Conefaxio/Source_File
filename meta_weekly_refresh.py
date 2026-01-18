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
# CONFIG v1.0.1
# =========================
VERSION = "1.0.1"
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
        "list

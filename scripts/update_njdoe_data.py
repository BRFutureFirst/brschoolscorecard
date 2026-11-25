#!/usr/bin/env python3
"""
BRRSD Civic Transparency: NJDOE School Performance Report ETL
==============================================================

Discovers, downloads, filters and normalizes the New Jersey Department of
Education (NJDOE) School Performance Report (SPR) databases for the
Bridgewater-Raritan Regional School District (35-0555) and its peer districts,
and writes:

    public/data/brrsdData.json        normalized dataset read by the dashboard
    public/data/exports/*.csv         flat CSV exports for residents and press
    data/extracts/<year>_<kind>.json.gz
                                      small filtered extracts of each NJDOE
                                      workbook (only our 5 districts' rows), so
                                      the whole dataset can be rebuilt without
                                      re-downloading hundreds of MB.

How discovery works
-------------------
NJDOE's download page (https://www.nj.gov/education/spr/download/) does not
contain plain <a href="...xlsx"> links. It lists the school years in a
<select id="schoolYear"> and builds the links in JavaScript
(script/download.js). The crawler therefore:

  1. reads the year <option>s with BeautifulSoup,
  2. fetches download.js and pulls the base URL, folder and file names out of
     it with regular expressions (falling back to the last known pattern),
  3. also probes one year past the newest listed year (the dropdown can lag
     behind the files),
  4. reads the archive page for the 2014-15 combined database,
  5. sends a HEAD request to every candidate URL and records Last-Modified and
     Content-Length. A year is re-processed only when that signature changes.

Usage
-----
    python scripts/update_njdoe_data.py              # incremental (new/updated years)
    python scripts/update_njdoe_data.py --backfill   # re-pull every year from 2014-15
    python scripts/update_njdoe_data.py --years 2024-25 --force
    python scripts/update_njdoe_data.py --rebuild    # offline: rebuild JSON/CSV from extracts
"""
from __future__ import annotations

import argparse
import csv
import difflib
import gzip
import json
import logging
import math
import re
import shutil
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import openpyxl
import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sys.path.insert(0, str(Path(__file__).resolve().parent))
import njdoe_tges as tges  # noqa: E402  (spending data: Taxpayers' Guide to Education Spending)
import njdoe_ufb as ufb  # noqa: E402  (User-Friendly Budget summaries)

LOG = logging.getLogger("njdoe_etl")

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "public" / "data"
DATA_JSON = DATA_DIR / "brrsdData.json"
EXPORT_DIR = DATA_DIR / "exports"
EXTRACT_DIR = ROOT / "data" / "extracts"
TGES_EXTRACT_DIR = EXTRACT_DIR / "tges"
STATEWIDE_DIR = EXTRACT_DIR / "statewide"
UFB_EXTRACT_DIR = EXTRACT_DIR / "ufb"

LANDING_URL = "https://www.nj.gov/education/spr/download/"
ARCHIVE_URL = "https://www.nj.gov/education/spr/download/archive/index.shtml"
FALLBACK_BASE = "https://www.nj.gov/education/sprreports/download/"
FALLBACK_FOLDER = "DataFiles"
FALLBACK_FILES = {
    "school": "Database_SchoolDetail.xlsx",
    "district": "Database_DistrictStateDetail.xlsx",
}
FIRST_YEAR = 2014  # 2014-15, the first PARCC year
USER_AGENT = "BRRSD-Transparency-ETL/1.0 (open civic-data pipeline; python-requests)"
SCHEMA_VERSION = 1
STATE_MSGP_MEDIAN = 50.0

TARGET_DISTRICT = "35-0555"
DISTRICTS: dict[str, dict] = {
    "35-0555": {"name": "Bridgewater-Raritan Regional", "short": "Bridgewater-Raritan", "role": "target", "grades": "PK-12"},
    "35-0350": {"name": "Bernards Township", "short": "Bernards", "role": "peer", "grades": "PK-12"},
    "35-3320": {"name": "Montgomery Township", "short": "Montgomery", "role": "peer", "grades": "PK-12"},
    "35-2170": {"name": "Hillsborough Township", "short": "Hillsborough", "role": "peer", "grades": "PK-12"},
    "35-5550": {"name": "Watchung Hills Regional", "short": "Watchung Hills", "role": "peer", "grades": "09-12"},
}

# Subgroups we publish. Each NJDOE label is normalized (lowercase, alphanumerics
# only) and matched against these patterns.
GROUPS = [
    ("All Students", re.compile(r"^(districtwide|schoolwide|allstudents|total|all)$")),
    ("Economically Disadvantaged", re.compile(r"^economicallydisadvantaged")),
    ("Students with Disabilities", re.compile(r"^studentswithdisabilit")),
]

DATASET_KEYS = {
    "proficiency": ("year", "districtCode", "schoolCode", "subject", "test", "group"),
    "growth": ("year", "districtCode", "schoolCode", "subject", "grade", "group"),
    "absenteeism": ("year", "districtCode", "schoolCode", "group"),
    "staffing": ("year", "districtCode", "schoolCode"),
    "highschool": ("year", "districtCode", "schoolCode", "metric", "group"),
}

# Priority tiers when the same fact appears in several places.
TIER_OWN_YEAR = 3      # value comes from that year's own report
TIER_TREND = 2         # value comes from a trend table in a later report
TIER_CROSS_LEVEL = 1.5 # district value printed inside a school-level file
TIER_DERIVED = 1       # value computed by this pipeline (weighted aggregate)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def norm(value) -> str:
    """Normalize a header/label: lowercase, alphanumerics only."""
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def norm_year(value, default: str | None = None) -> str | None:
    """Normalize '2021-2022', '2022-23', '1415', '2014-15' to 'YYYY-YY'."""
    if value is None:
        return default
    s = str(value).strip()
    m = re.match(r"^(\d{4})\s*[-–/]\s*(\d{2}|\d{4})$", s)
    if m:
        y = int(m.group(1))
        return f"{y}-{str(y + 1)[2:]}"
    m = re.match(r"^(\d{2})(\d{2})$", s)
    if m and (int(m.group(1)) + 1) % 100 == int(m.group(2)):
        y = 2000 + int(m.group(1))
        return f"{y}-{str(y + 1)[2:]}"
    return default


def long_year(year: str) -> str:
    """'2023-24' -> '2023-2024' (the folder naming NJDOE uses)."""
    y = int(year[:4])
    return f"{y}-{y + 1}"


def end_year(year: str) -> int:
    return int(year[:4]) + 1


def era_of(year: str) -> str:
    """Testing era by spring test administration year."""
    spring = end_year(year)
    if spring <= 2019:
        return "PARCC"
    if spring <= 2021:
        return "COVID_WAIVER"
    return "NJSLA"


_SUPPRESSED = {"", "*", "**", "n", "na", "n/a", "s", "-", "--", "nan", "none", "null"}


def num(value) -> float | None:
    """Parse an NJDOE cell to float. Suppressed, censored or blank values give None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if (isinstance(value, float) and math.isnan(value)) else float(value)
    s = str(value).strip().replace(",", "").replace("%", "").replace("†", "").strip()
    if s.lower() in _SUPPRESSED:
        return None
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*:\s*1", s)  # ratios such as '11:1'
    if m:
        return float(m.group(1))
    m = re.fullmatch(r"([<>≤≥]=?)?\s*(-?\d+(?:\.\d+)?)", s)
    if m:
        return None if m.group(1) else float(m.group(2))  # '<10' is censored
    return None


def code(value, width: int) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    return s.zfill(width) if s.isdigit() else ""


def canon_group(label) -> str | None:
    n = norm(label)
    for name, rx in GROUPS:
        if rx.search(n):
            return name
    return None


def canon_test(*labels) -> str | None:
    """Normalize a grade/course label to 'Grade 5', 'Algebra I', 'Algebra II', 'Geometry'."""
    for label in labels:
        n = norm(label)
        if not n:
            continue
        if "alg02" in n or n.endswith("algebraii"):
            return "Algebra II"
        if "alg01" in n or n.endswith("algebrai"):
            return "Algebra I"
        if "geo" in n:
            return "Geometry"
        m = re.search(r"(\d{1,2})$", n)
        if m and 3 <= int(m.group(1)) <= 11:
            return f"Grade {int(m.group(1))}"
    return None


def canon_subject(text) -> str | None:
    """Pick the subject that appears earliest in a sheet title or label."""
    t = norm(text)
    hits = []
    for subj, rx in (("ELA", r"ela|lal|englishlanguage|literacy"), ("Math", r"math|^alg|^geo"),
                     ("Science", r"science|biology|njask")):
        m = re.search(rx, t)
        if m:
            hits.append((m.start(), subj))
    return min(hits)[1] if hits else None


def pick(cols, patterns, exclude=(), level: str | None = None, allow_other_level=False) -> str | None:
    """Find a column by regex priority list, with level (school/district) preference
    and a difflib fuzzy fallback for small header variations across years."""
    excl = [re.compile(e) for e in exclude]
    usable = [c for c in cols if not any(e.search(c) for e in excl)]
    for pat in patterns:
        rx = re.compile(pat)
        matches = [c for c in usable if rx.search(c)]
        if not matches:
            continue
        if not level:
            return matches[0]
        other = "district" if level == "school" else "school"
        own = [c for c in matches if level in c.replace("schoolyear", "")]
        neutral = [c for c in matches if level not in c and other not in c.replace("schoolyear", "")]
        oth = [c for c in matches if c not in own and c not in neutral]
        if own:
            return own[0]
        if neutral:
            return neutral[0]
        if oth and allow_other_level:
            return oth[0]
    # Fuzzy fallback for plain-literal patterns (e.g. 'validscores' vs 'validscore').
    for pat in patterns:
        if re.fullmatch(r"[a-z0-9]+", pat):
            hit = difflib.get_close_matches(pat, usable, n=1, cutoff=0.85)
            if hit:
                LOG.debug("fuzzy column match %r -> %r", pat, hit[0])
                return hit[0]
    return None


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=5, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=("GET", "HEAD"), raise_on_status=False)
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers["User-Agent"] = USER_AGENT
    return s


def probe(session: requests.Session, url: str) -> dict | None:
    """HEAD a candidate file. Returns its signature, or None if it does not exist."""
    try:
        r = session.head(url, allow_redirects=True, timeout=60)
        if r.status_code in (403, 405):  # server refuses HEAD: fall back to a streamed GET
            r = session.get(url, stream=True, timeout=60)
            r.close()
    except requests.RequestException as exc:
        LOG.warning("probe failed for %s: %s", url, exc)
        return None
    ctype = r.headers.get("Content-Type", "")
    if r.status_code != 200 or "html" in ctype.lower():
        return None
    length = r.headers.get("Content-Length")
    return {
        "url": url,
        "lastModified": r.headers.get("Last-Modified"),
        "etag": r.headers.get("ETag"),
        "contentLength": int(length) if length and length.isdigit() else None,
    }


def discover_sources(session: requests.Session) -> dict[str, dict[str, dict]]:
    """Return {year: {kind: signature}} for every downloadable SPR database."""
    years: set[str] = set()
    base, folder, files = FALLBACK_BASE, FALLBACK_FOLDER, dict(FALLBACK_FILES)
    candidates: dict[str, dict[str, str]] = defaultdict(dict)

    try:
        resp = session.get(LANDING_URL, timeout=60)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for opt in soup.select("select option"):
            y = norm_year(opt.get("value") or opt.get_text())
            if y:
                years.add(y)
        # If NJDOE ever switches back to static anchors, honor them directly.
        for a in soup.find_all("a", href=True):
            href = urljoin(LANDING_URL, a["href"])
            m = re.search(r"/(\d{4}-\d{4})/[^/]+\.xlsx$", href, re.I)
            if m and "layout" not in href.lower():
                kind = "district" if "district" in href.lower() else "school"
                candidates[norm_year(m.group(1))][kind] = href
        for script in soup.find_all("script", src=True):
            if "download" not in script["src"].lower():
                continue
            js = session.get(urljoin(LANDING_URL, script["src"]), timeout=60).text
            bases = re.findall(r"baseUrl\s*=\s*['\"`](https?://[^'\"`]+)['\"`]", js)
            if bases:
                base = bases[-1] if bases[-1].endswith("/") else bases[-1] + "/"
            m = re.search(r"\$\{baseUrl\}([A-Za-z0-9_]+)/\$\{year\}", js)
            if m:
                folder = m.group(1)
            m = re.search(r"school:\s*['\"]([^'\"]+\.xlsx)['\"]\s*,\s*district:\s*['\"]([^'\"]+\.xlsx)['\"]", js)
            if m:
                files = {"school": m.group(1), "district": m.group(2)}
            LOG.info("download.js -> base=%s folder=%s files=%s", base, folder, files)
    except requests.RequestException as exc:
        LOG.warning("Could not read the NJDOE landing page (%s); using the known URL pattern.", exc)

    if not years:  # landing page unreadable: guess from the calendar
        today = datetime.now(timezone.utc)
        last = today.year - 1 if today.month >= 7 else today.year - 2
        years = {f"{y}-{str(y + 1)[2:]}" for y in range(2015, last + 1)}
    # The dropdown can lag behind newly posted files, so probe one year ahead.
    newest = max(int(y[:4]) for y in years)
    years.add(f"{newest + 1}-{str(newest + 2)[2:]}")

    for y in sorted(years):
        if int(y[:4]) < FIRST_YEAR:
            continue
        for kind, fname in files.items():
            candidates[y].setdefault(kind, f"{base}{folder}/{long_year(y)}/{fname}")

    # Archive page: 2014-15 (and earlier) combined school/district databases.
    try:
        resp = session.get(ARCHIVE_URL, timeout=60)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = urljoin(ARCHIVE_URL, a["href"])
            low = href.lower()
            if not re.search(r"\.xlsx?$", low) or "layout" in low or "database" not in low.replace("%20", " "):
                continue
            m = re.search(r"/(20\d{2})(\d{2})/", href)
            if not m:
                continue
            y = norm_year(f"{m.group(1)}-{m.group(2)}")
            if y and int(y[:4]) >= FIRST_YEAR and y not in candidates:
                candidates[y]["school"] = href
    except requests.RequestException as exc:
        LOG.warning("Could not read the NJDOE archive page: %s", exc)

    found: dict[str, dict[str, dict]] = {}
    for y in sorted(candidates):
        for kind, url in candidates[y].items():
            sig = probe(session, url.replace(" ", "%20"))
            if sig:
                found.setdefault(y, {})[kind] = sig
    LOG.info("Discovered %d reporting years: %s", len(found),
             ", ".join(f"{y}[{'+'.join(sorted(k))}]" for y, k in found.items()))
    return found


def download(session: requests.Session, sig: dict, dest: Path) -> Path:
    if dest.exists() and sig.get("contentLength") and dest.stat().st_size == sig["contentLength"]:
        LOG.info("  reusing %s", dest.name)
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    started = time.time()
    with session.get(sig["url"], stream=True, timeout=(30, 600)) as r:
        r.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
    with open(tmp, "rb") as fh:
        if fh.read(2) != b"PK":
            tmp.unlink(missing_ok=True)
            raise ValueError(f"{sig['url']} is not an .xlsx (zip) file")
    tmp.replace(dest)
    LOG.info("  downloaded %s (%.1f MB in %.0fs)", dest.name, dest.stat().st_size / 1e6, time.time() - started)
    return dest


# --------------------------------------------------------------------------- #
# Extraction: stream a workbook and keep only our districts' rows
# --------------------------------------------------------------------------- #
COUNTY_KEYS = {"countycode", "cocode", "county"}
DISTRICT_KEYS = {"districtcode", "distcode", "district"}
SCHOOL_KEYS = {"schoolcode", "schcode"}


def sheet_is_relevant(title_n: str) -> bool:
    if re.search(r"header|^enrollment(trends)?bygrade$|^enrollment$", title_n):
        return True
    return any(ex.sheet_rx.search(title_n) and not (ex.skip_rx and ex.skip_rx.search(title_n))
               for ex in EXTRACTORS)


def extract_workbook(path: Path, kind: str, file_year: str, statewide: dict | None = None) -> dict:
    """Keep our districts' rows. If `statewide` is a dict, also fill it with every NJ
    district's district-level rows for the sheets the statewide benchmarks need."""
    started = time.time()
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = {"fileYear": file_year, "kind": kind, "sheets": {}, "schoolNames": {}}
    for ws in wb.worksheets:
        title_n = norm(ws.title)
        if not sheet_is_relevant(title_n):
            continue
        try:
            ws.reset_dimensions()  # NJDOE files sometimes declare a wrong sheet size
        except AttributeError:
            pass
        header, ci, di, si, rows, all_rows = None, None, None, None, [], []
        want_all = statewide is not None and statewide_sheet(title_n)
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if header is None:
                if i > 25:
                    break
                cells = [norm(c) for c in row]
                cidx = next((j for j, c in enumerate(cells) if c in COUNTY_KEYS), None)
                didx = next((j for j, c in enumerate(cells) if c in DISTRICT_KEYS), None)
                if cidx is not None and didx is not None:
                    seen: dict[str, int] = {}
                    header = []
                    for c in cells:
                        if c and c in seen:
                            seen[c] += 1
                            c = f"{c}_{seen[c]}"
                        elif c:
                            seen[c] = 0
                        header.append(c)
                    ci, di = cidx, didx
                    si = next((j for j, c in enumerate(cells) if c in SCHOOL_KEYS), None)
                continue
            if row is None or len(row) <= max(ci, di):
                continue
            cc, dc = code(row[ci], 2), code(row[di], 4)
            dkey = f"{cc}-{dc}"
            ours = dkey in DISTRICTS
            school = code(row[si], 3) if si is not None and si < len(row) else ""
            if not ours and not (want_all and cc and dc and not school):
                continue
            rec = {}
            for h, v in zip(header, row):
                if h and v is not None:
                    rec[h] = v.strip() if isinstance(v, str) else v
            rec["__d"] = dkey
            rec["__s"] = school or None
            if want_all and not school:
                all_rows.append(rec)
            if not ours:
                continue
            rows.append(rec)
            if school and rec.get("schoolname"):
                out["schoolNames"][f"{dkey}-{school}"] = str(rec["schoolname"])
        if header and rows:
            out["sheets"][ws.title] = {"columns": [h for h in header if h], "rows": rows}
        if header and all_rows:
            statewide[ws.title] = {"columns": [h for h in header if h], "rows": all_rows}
    wb.close()
    LOG.info("  extracted %d sheets from %s in %.0fs", len(out["sheets"]), path.name, time.time() - started)
    return out


def extract_path(year: str, kind: str) -> Path:
    return EXTRACT_DIR / f"{year}_{kind}.json.gz"


def save_extract(data: dict, year: str, kind: str) -> None:
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, sort_keys=True, default=str, separators=(",", ":")).encode()
    # mtime=0 keeps the gzip byte-identical across runs (clean git diffs)
    with open(extract_path(year, kind), "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(payload)


def load_extract(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
# Normalization: semantic extractors that tolerate NJDOE's schema drift
# --------------------------------------------------------------------------- #
class Store:
    """Keeps the highest-priority value for every fact key."""

    def __init__(self):
        self.data: dict[str, dict[tuple, tuple]] = defaultdict(dict)

    def add(self, dataset: str, rec: dict, priority: tuple) -> None:
        key = tuple(rec.get(k) for k in DATASET_KEYS[dataset])
        cur = self.data[dataset].get(key)
        if dataset == "staffing" and cur is not None:
            merged = dict(cur[1])
            for k, v in rec.items():
                if v is not None and (merged.get(k) is None or priority > cur[0]):
                    merged[k] = v
            self.data[dataset][key] = (max(priority, cur[0]), merged)
            return
        if cur is None or priority > cur[0]:
            self.data[dataset][key] = (priority, rec)

    def records(self, dataset: str) -> list[dict]:
        return [r for _, r in self.data[dataset].values()]


class Ctx:
    def __init__(self, file_year: str, kind: str, sheet: str, cols: list[str]):
        self.file_year, self.kind, self.sheet, self.cols = file_year, kind, sheet, cols
        self.sheet_n = norm(sheet)

    def level_of(self, row) -> str:
        return "school" if row.get("__s") else "district"

    def col(self, patterns, exclude=(), level=None):
        return pick(self.cols, patterns, exclude, level, allow_other_level=(level == "district"))

    def year_of(self, row, ycol) -> str:
        return norm_year(row.get(ycol), self.file_year) if ycol else self.file_year

    def priority(self, year: str, tier=None) -> tuple:
        t = tier if tier is not None else (TIER_OWN_YEAR if year == self.file_year else TIER_TREND)
        return (t, self.file_year)


def base_rec(row, year: str) -> dict:
    return {"year": year, "era": era_of(year), "districtCode": row["__d"], "schoolCode": row.get("__s")}


class Extractor:
    def __init__(self, name, sheet_rx, fn, skip_rx=None):
        self.name, self.fn = name, fn
        self.sheet_rx = re.compile(sheet_rx)
        self.skip_rx = re.compile(skip_rx) if skip_rx else None

    def matches(self, title_n: str) -> bool:
        return bool(self.sheet_rx.search(title_n)) and not (self.skip_rx and self.skip_rx.search(title_n))


def _value_cols(ctx: Ctx, level: str, patterns, exclude):
    return ctx.col(patterns, exclude, level)


def ex_prof_summary(ctx: Ctx, rows, store: Store):
    """Subject-level proficiency by subgroup (ELA / Math participation & performance tables)."""
    ycol = ctx.col([r"^schoolyear$", r"^year$", r"^datayear$"])
    gcol = ctx.col([r"^studentgroups?$", r"^subgroup$", r"^type$"])
    scol = ctx.col([r"^subject$"])
    if not gcol:
        return
    excl = (r"state", r"federal", r"target", r"percentile", r"participation")
    by_level = {}
    for lvl in ("district", "school"):
        by_level[lvl] = (
            _value_cols(ctx, lvl, [r"metexceededexpectations", r"^(district|school)performance$",
                                   r"testersmet", r"perfmetstandards", r"percentmeetingstandards",
                                   r"performance"], excl),
            ctx.col([r"validscores"], (r"state",), lvl),
        )
    state_col = ctx.col([r"(metexceed|performance|testersmet).*state", r"state.*(metexceed|performance|testersmet)"],
                        (r"federal",))
    for row in rows:
        group = canon_group(row.get(gcol))
        if not group:
            continue
        subject = canon_subject(row.get(scol)) if scol and row.get(scol) else canon_subject(ctx.sheet)
        if subject not in ("ELA", "Math"):
            continue
        vcol, ncol = by_level[ctx.level_of(row)]
        rate = num(row.get(vcol)) if vcol else None
        if rate is None:
            continue
        year = ctx.year_of(row, ycol)
        rec = base_rec(row, year) | {
            "subject": subject, "test": "All", "group": group,
            "validScores": num(row.get(ncol)) if ncol else None,
            "rate": rate, "stateRate": num(row.get(state_col)) if state_col else None,
            "source": f"{ctx.file_year}:{ctx.sheet}",
        }
        store.add("proficiency", rec, ctx.priority(year))


def ex_prof_by_test(ctx: Ctx, rows, store: Store):
    """Proficiency by grade / end-of-course test (incl. Algebra I) and subgroup."""
    ycol = ctx.col([r"^schoolyear$", r"^datayear$", r"^year$"])
    tcol = ctx.col([r"^gradetest$", r"^gradesubject$", r"^grade$"])
    subcol = ctx.col([r"^subject$"])
    gcol = ctx.col([r"^studentgroups?$", r"^subgroup$", r"^type$"])
    if not gcol or not (tcol or subcol):
        return
    excl = (r"state", r"mean", r"level\d")
    cols = {}
    for lvl in ("district", "school"):
        cols[lvl] = dict(
            v=ctx.col([r"^metexceed$", r"proficiencyrate", r"testersmetorexceeded", r"metexceed"], excl, lvl),
            n=ctx.col([r"validscores"], (r"state",), lvl),
            l4=ctx.col([r"level4"], (r"state",), lvl),
            l5=ctx.col([r"level5"], (r"state",), lvl),
        )
    state_col = ctx.col([r"statemetexceed", r"statetestersmet", r"proficiencyratestate"])
    subject = canon_subject(ctx.sheet)
    for row in rows:
        group = canon_group(row.get(gcol))
        test = canon_test(row.get(tcol) if tcol else None, row.get(subcol) if subcol else None)
        if not group or not test:
            continue
        c = cols[ctx.level_of(row)]
        rate = num(row.get(c["v"])) if c["v"] else None
        if rate is None and c["l4"] and c["l5"]:
            l4, l5 = num(row.get(c["l4"])), num(row.get(c["l5"]))
            rate = l4 + l5 if l4 is not None and l5 is not None else None
        if rate is None:
            continue
        subj = "Math" if test.startswith(("Algebra", "Geometry")) else subject
        year = ctx.year_of(row, ycol)
        rec = base_rec(row, year) | {
            "subject": subj, "test": test, "group": group,
            "validScores": num(row.get(c["n"])) if c["n"] else None,
            "rate": rate, "stateRate": num(row.get(state_col)) if state_col else None,
            "source": f"{ctx.file_year}:{ctx.sheet}",
        }
        store.add("proficiency", rec, ctx.priority(year))


def ex_science(ctx: Ctx, rows, store: Store):
    """NJSLA-Science (levels 3+4 = proficient) plus legacy NJASK/NJBCT science."""
    ycol = ctx.col([r"^schoolyear$"])
    gcol = ctx.col([r"^studentgroups?$", r"^subgroup$"])
    grcol = ctx.col([r"^grade$", r"^gradelevel$"])
    overall = "overall" in ctx.sheet_n
    legacy = "njask" in ctx.sheet_n or "biology" in ctx.sheet_n
    cols = {}
    for lvl in ("district", "school"):
        cols[lvl] = dict(
            l3=ctx.col([r"^level3$", r"level3"], (r"state",), lvl),
            l4=ctx.col([r"^level4$", r"level4"], (r"state",), lvl),
            prof=ctx.col([r"percentageproficient"], (r"state",), lvl),
            ap=ctx.col([r"^advancedproficient$"], (r"state",), lvl),
            p=ctx.col([r"^proficient$"], (r"state",), lvl),
        )
    st3 = ctx.col([r"level3.*state"])
    st4 = ctx.col([r"level4.*state"])
    stp = ctx.col([r"percentageproficientstate"])
    for row in rows:
        group = canon_group(row.get(gcol)) if gcol else "All Students"
        if not group:
            continue
        c = cols[ctx.level_of(row)]
        rate = None
        if c["prof"]:
            rate = num(row.get(c["prof"]))
        elif c["l3"] and c["l4"]:
            l3, l4 = num(row.get(c["l3"])), num(row.get(c["l4"]))
            rate = l3 + l4 if l3 is not None and l4 is not None else None
        elif legacy and c["ap"] and c["p"]:
            ap, p = num(row.get(c["ap"])), num(row.get(c["p"]))
            rate = ap + p if ap is not None and p is not None else None
        if rate is None:
            continue
        if overall:
            test = "All"
        elif "biology" in ctx.sheet_n:
            test = "Biology"
        else:
            test = canon_test(row.get(grcol)) if grcol else None
            if test is None and grcol and str(row.get(grcol, "")).strip().isdigit():
                test = f"Grade {int(str(row[grcol]).strip())}"
        if not test:
            continue
        state = None
        if stp:
            state = num(row.get(stp))
        elif st3 and st4:
            a, b = num(row.get(st3)), num(row.get(st4))
            state = a + b if a is not None and b is not None else None
        year = ctx.year_of(row, ycol)
        rec = base_rec(row, year) | {
            "subject": "Science", "test": test, "group": group, "validScores": None,
            "rate": round(rate, 1), "stateRate": state,
            "assessment": "NJASK/NJBCT" if legacy else "NJSLA-S",
            "source": f"{ctx.file_year}:{ctx.sheet}",
        }
        store.add("proficiency", rec, ctx.priority(year))


def ex_growth(ctx: Ctx, rows, store: Store):
    """Median Student Growth Percentiles, long (subject column) or wide (ELA_/Math_ columns)."""
    ycol = ctx.col([r"^schoolyear$"])
    subj_col = ctx.col([r"^subject$", r"^elamath$", r"^studentgrowth$"])
    gcol = ctx.col([r"^studentgroups?$", r"^subgroup$"])
    grcol = ctx.col([r"^grade$"])
    excl = (r"state", r"category", r"level", r"met", r"target")

    def emit(row, subject, value, state, level_override=None):
        group = canon_group(row.get(gcol)) if gcol else "All Students"
        if not group or value is None:
            return
        grade = canon_test(row.get(grcol)) if grcol else None
        year = ctx.year_of(row, ycol)
        rec = base_rec(row, year) | {
            "subject": subject, "grade": grade or "All", "group": group,
            "msgp": value, "stateMsgp": state, "source": f"{ctx.file_year}:{ctx.sheet}",
        }
        tier = None
        if level_override == "district":
            rec["schoolCode"] = None
            tier = TIER_CROSS_LEVEL
        store.add("growth", rec, ctx.priority(year, tier))

    if subj_col:
        vcols = {lvl: ctx.col([r"^msgp", r"median", r"sgp$"], excl, lvl) for lvl in ("district", "school")}
        state_col = ctx.col([r"state.*median", r"statewidemedian", r"median.*state"], (r"category",))
        dist_in_school = ctx.col([r"^districtmedian$"]) if ctx.kind == "school" else None
        for row in rows:
            subject = canon_subject(row.get(subj_col))
            if subject not in ("ELA", "Math"):
                continue
            lvl = ctx.level_of(row)
            emit(row, subject, num(row.get(vcols[lvl])) if vcols[lvl] else None,
                 num(row.get(state_col)) if state_col else None)
            if lvl == "school" and dist_in_school and not grcol:
                emit(row, subject, num(row.get(dist_in_school)), num(row.get(state_col)) if state_col else None,
                     level_override="district")
    else:  # wide layout
        for subject, prefix in (("ELA", "ela"), ("Math", "math")):
            sub = [c for c in ctx.cols if c.startswith(prefix)]
            if not sub:
                continue
            vcols = {lvl: pick(sub, [r"msgp", r"median", r"sgp"], excl, lvl, allow_other_level=(lvl == "district"))
                     for lvl in ("district", "school")}
            state_col = pick(sub, [r"median.*state$", r"state"], (r"category",))
            dcol = pick(sub, [r"median.*district$"], (r"category",)) if ctx.kind == "school" else None
            for row in rows:
                lvl = ctx.level_of(row)
                emit(row, subject, num(row.get(vcols[lvl])) if vcols[lvl] else None,
                     num(row.get(state_col)) if state_col else None)
                if lvl == "school" and dcol and not grcol:
                    emit(row, subject, num(row.get(dcol)), num(row.get(state_col)) if state_col else None,
                         level_override="district")


def ex_absenteeism(ctx: Ctx, rows, store: Store):
    ycol = ctx.col([r"^schoolyear$"])
    gcol = ctx.col([r"^studentgroups?$", r"^subgroup$"])
    excl = (r"state", r"count", r"target", r"chronicallyabsent", r"met")
    vcols = {lvl: ctx.col([r"chronicabspct", r"chronicabsenteeismrate", r"percentchronicabsent",
                           r"chronicabsenteeism$", r"^percent$"], excl, lvl) for lvl in ("district", "school")}
    state_col = ctx.col([r"statewide.*rate", r"rate.*state$", r"^stateabs", r"state.*pct"], (r"target", r"essa"))
    for row in rows:
        group = canon_group(row.get(gcol)) if gcol else "All Students"
        vcol = vcols[ctx.level_of(row)]
        value = num(row.get(vcol)) if vcol else None
        if not group or value is None:
            continue
        year = ctx.year_of(row, ycol)
        rec = base_rec(row, year) | {
            "group": group, "rate": value,
            "stateRate": num(row.get(state_col)) if state_col else None,
            "source": f"{ctx.file_year}:{ctx.sheet}",
        }
        store.add("absenteeism", rec, ctx.priority(year))


def ex_staffing(ctx: Ctx, rows, store: Store):
    s = ctx.sheet_n
    ycol = ctx.col([r"^schoolyear$"])
    for row in rows:
        lvl = ctx.level_of(row)
        year = ctx.year_of(row, ycol)
        fields = {}
        if s == "teachersexperience":
            c = ctx.col([r"^teachercount"], (r"state",), lvl)
            fields["teachers"] = num(row.get(c)) if c else None
        elif s == "administratorsexperience":
            c = ctx.col([r"^admincount"], (r"state",), lvl)
            fields["administrators"] = num(row.get(c)) if c else None
        elif s == "staffcounts":
            cat = norm(row.get(ctx.col([r"staffcategory"])))
            c = ctx.col([r"totalstaff"], (r"state",), lvl)
            if cat.startswith("teacher"):
                fields["teachers"] = num(row.get(c))
            elif cat.startswith("administrator"):
                fields["administrators"] = num(row.get(c))
        elif re.search(r"ratio|climate", s):
            st = ctx.col([r"^studentteacher", r"facultystudent", r"^teacherratio"], (r"state",), lvl)
            sa = ctx.col([r"^studentadmin", r"^adminstudent", r"^administratorsratio"], (r"state",), lvl)
            fields["studentTeacherRatio"] = num(row.get(st)) if st else None
            fields["studentAdminRatio"] = num(row.get(sa)) if sa else None
        elif re.search(r"enrollment", s):
            c = ctx.col([r"^total$", r"^rowtotal$"])
            fields["enrollment"] = num(row.get(c)) if c else None
        fields = {k: v for k, v in fields.items() if v is not None}
        if not fields:
            continue
        rec = base_rec(row, year) | fields
        store.add("staffing", rec, ctx.priority(year))


# --------------------------------------------------------------------------- #
# High school outcomes: AP/IB, dual enrollment, graduation, SAT
# --------------------------------------------------------------------------- #
def _fcol(ctx: Ctx, rx: str) -> str | None:
    return next((c for c in ctx.cols if re.search(rx, c)), None)


def _cohort_year(y: int) -> str:
    """Class of 2024 -> school year 2023-24."""
    return f"{y - 1}-{str(y)[2:]}"


def _hs_add(store: Store, ctx: Ctx, row, year: str, metric: str, group: str, value, state, tier=None):
    if value is None or not group:
        return
    rec = base_rec(row, year) | {"metric": metric, "group": group, "value": value, "stateValue": state,
                                 "source": f"{ctx.file_year}:{ctx.sheet}"}
    store.add("highschool", rec, ctx.priority(year, tier))


def ex_apib(ctx: Ctx, rows, store: Store):
    """AP/IB course and exam participation, AP 3+ / IB 4+ results, dual enrollment."""
    ycol = ctx.col([r"^schoolyear$"])
    gcol = ctx.col([r"^studentgroups?$"])
    cat = ctx.col([r"^reportcategory$"])
    if cat:  # 2016-17: long layout
        vcol, scol = ctx.col([r"^districtpercent$", r"^schoolpercent$"]), ctx.col([r"^statepercent$"])
        for row in rows:
            c = norm(row.get(cat))
            # Check "dual" first: "...DualEnrollmentCourse" also contains "course".
            metric = ("dualEnroll" if "dual" in c else "apScore3" if re.search(r"score", c)
                      else "apExam" if re.search(r"exam", c) else "apCourse" if re.search(r"course", c) else None)
            if metric and vcol:
                _hs_add(store, ctx, row, ctx.year_of(row, ycol), metric, "All Students",
                        num(row.get(vcol)), num(row.get(scol)) if scol else None)
        return
    specs = {
        "apCourse": (r"^apib(course|enrolled)(district|school)$|^enrolledinoneormoreaporibcourse$", r"^apib(course|enrolled)state$|^stateenrolledinoneormoreaporibcourse$"),
        "apExam": (r"^apibexams?(district|school)$", r"^apibexams?state$"),
        "apScore3": (r"^ap3ib4(district|school)$", r"^ap3ib4state$"),
        "dualEnroll": (r"^dual(district|school)$|^enrolledinoneormoredualenrollmentcourse$", r"^dualstate$|^stateenrolledinoneormoredualenrollmentcourse$"),
    }
    cols = {m: (_fcol(ctx, v), _fcol(ctx, s)) for m, (v, s) in specs.items()}
    for row in rows:
        group = canon_group(row.get(gcol)) if gcol else "All Students"
        year = ctx.year_of(row, ycol)
        for m, (v, s) in cols.items():
            if v:
                _hs_add(store, ctx, row, year, m, group, num(row.get(v)), num(row.get(s)) if s else None)


def ex_grad(ctx: Ctx, rows, store: Store):
    """4- and 5-year adjusted cohort graduation rates (NJ state method), by cohort year."""
    gcol = ctx.col([r"^studentgroups?$"])
    cy = ctx.col([r"^cohortyear$"])
    if cy:  # long trend layout (2016-17 .. 2023-24): CohortYear, rate type, value
        tcol = ctx.col([r"^graduationratetype$"]) or ctx.col([r"^graduationrate$"])
        vcol = ctx.col([r"^graduationrate$"]) if tcol == ctx.col([r"^graduationratetype$"]) else ctx.col([r"^gradrateperc$"])
        scol = ctx.col([r"^statewidegraduationrate$"])
        for row in rows:
            m = re.match(r"([45])", norm(row.get(tcol)))
            y = num(row.get(cy))
            if m and y and vcol:
                _hs_add(store, ctx, row, _cohort_year(int(y)), f"grad{m.group(1)}", "All Students",
                        num(row.get(vcol)), num(row.get(scol)) if scol else None)
        return
    if _fcol(ctx, r"^4yrcohort$"):  # 2024-25+: wide trend layout by student group
        for row in rows:
            group = canon_group(row.get(gcol)) if gcol else "All Students"
            for n in ("4", "5"):
                m = re.search(r"(20\d\d)", str(row.get(f"{n}yrcohort") or ""))
                v, s = _fcol(ctx, rf"^{n}yrgraduationratestate(district|school)$"), _fcol(ctx, rf"^{n}yrgraduationratestatestate$")
                if m and v:
                    _hs_add(store, ctx, row, _cohort_year(int(m.group(1))), f"grad{n}", group,
                            num(row.get(v)), num(row.get(s)) if s else None)
        return
    if "cohortprofile" in ctx.sheet_n:  # 2019-20 .. 2023-24: subgroup rates (graduates %)
        n = "4" if ctx.sheet_n.startswith("4yr") else "5" if ctx.sheet_n.startswith("5yr") else None
        v, s = ctx.col([r"^graduates$"]), ctx.col([r"^stategraduates$"])
        if not n or not v:
            return
        fy = int(ctx.file_year[:4]) + 1  # class graduating in the file's year
        year = _cohort_year(fy if n == "4" else fy - 1)
        for row in rows:
            _hs_add(store, ctx, row, year, f"grad{n}", canon_group(row.get(gcol)) if gcol else "All Students",
                    num(row.get(v)), num(row.get(s)) if s else None, tier=TIER_TREND)
        return
    # 2016-17 .. 2018-19 'GraduationRates': one column per class and rate type.
    vals, states = {}, {}
    for c in ctx.cols:
        m = re.fullmatch(r"(district)?class(of)?(20\d\d)([45])y(ea)?r(rate|school|district)", c)
        if m:
            vals[c] = (int(m.group(3)), m.group(4))
        m = re.fullmatch(r"stateclass(of)?(20\d\d)([45])yearrate|class(20\d\d)([45])yrstate", c)
        if m:
            states[(int(m.group(2) or m.group(4)), m.group(3) or m.group(5))] = c
    for row in rows:
        group = canon_group(row.get(gcol)) if gcol else "All Students"
        for c, (y, n) in vals.items():
            s = states.get((y, n))
            _hs_add(store, ctx, row, _cohort_year(y), f"grad{n}", group, num(row.get(c)),
                    num(row.get(s)) if s else None, tier=TIER_TREND)


def ex_sat(ctx: Ctx, rows, store: Store):
    """SAT average scores, % meeting the college-readiness benchmark, and SAT participation."""
    ycol = ctx.col([r"^schoolyear$"])
    test, subj = ctx.col([r"^test$"]), ctx.col([r"^subject$"])
    if not test:  # participation sheet (one column per test)
        v, s = _fcol(ctx, r"^sat(district|school)?$"), _fcol(ctx, r"^(statesat|satstate)$")
        for row in rows:
            if v:
                _hs_add(store, ctx, row, ctx.year_of(row, ycol), "satParticipation", "All Students",
                        num(row.get(v)), num(row.get(s)) if s else None)
        return
    avg, savg = _fcol(ctx, r"^(district|school)avg$|^averagescore(district|school)$"), _fcol(ctx, r"^stateavg$|^averagescorestate$")
    bt, sbt = _fcol(ctx, r"^btpct$|^studentsmeetingbenchmark(district|school)$"), _fcol(ctx, r"^statebtpct$|^studentsmeetingbenchmarkstate$")
    for row in rows:
        if norm(row.get(test)) != "sat":
            continue
        s = norm(row.get(subj))
        key = "satMath" if s == "math" else "satEbrw" if "reading" in s else None
        if not key:
            continue
        year = ctx.year_of(row, ycol)
        if avg:
            _hs_add(store, ctx, row, year, key, "All Students", num(row.get(avg)), num(row.get(savg)) if savg else None)
        if bt:
            _hs_add(store, ctx, row, year, f"{key}Bench", "All Students", num(row.get(bt)), num(row.get(sbt)) if sbt else None)


EXTRACTORS = [
    Extractor("prof_summary", r"^(ela|lal|math|parccela|parccmath).*(particip|metexc)|^eseawaiver$", ex_prof_summary),
    Extractor("prof_by_test", r"^(ela|lal|math).*(bygrade|bytest)|^parcc(ela|math)(distribution)?$", ex_prof_by_test,
              skip_rx=r"trend|growth"),
    Extractor("science", r"science|njask|biology", ex_science, skip_rx=r"course|graph|summarybygrade$"),
    Extractor("growth", r"sgp|growth", ex_growth, skip_rx=r"perform|level|target"),
    Extractor("absenteeism", r"absent", ex_absenteeism, skip_rx=r"grade|^absenteeism$|daysabsent|target"),
    Extractor("staffing", r"^teachersexperience$|^administratorsexperience$|^staffcounts$|^studenttostaffratios$"
                          r"|climate|^enrollment(trends)?bygrade$|^enrollment$", ex_staffing),
    Extractor("apib", r"^apib(coursework|dual)", ex_apib),
    Extractor("grad", r"graduat", ex_grad, skip_rx=r"pathway|target|federal|accountability|6yr|^graduationcohortprofile$"),
    Extractor("sat", r"^psatsatact", ex_sat),
]


# --------------------------------------------------------------------------- #
# Derived metrics
# --------------------------------------------------------------------------- #
def weighted(items):
    """items: [(value, weight)] -> weighted mean, or None."""
    items = [(v, w) for v, w in items if v is not None and w]
    total = sum(w for _, w in items)
    return round(sum(v * w for v, w in items) / total, 1) if total else None


def derive(store: Store) -> None:
    prof = store.data["proficiency"]

    # 1. District proficiency from school rows when NJDOE published no district table
    #    (2014-15 and 2015-16 only shipped a school-level database).
    buckets = defaultdict(list)
    for (year, d, s, subj, test, grp), (_, r) in list(prof.items()):
        if s and r.get("validScores"):
            buckets[(year, d, subj, test, grp)].append(r)
    for (year, d, subj, test, grp), recs in buckets.items():
        if (year, d, None, subj, test, grp) in prof:
            continue
        # Only aggregate when every school that tested 'All Students' also reported this
        # group; otherwise a suppressed school would silently drop out (e.g. 2014-15
        # Algebra I SWD would become "13 accelerated middle-schoolers = 85%").
        tested = {r["schoolCode"] for r in buckets.get((year, d, subj, test, "All Students"), [])}
        if {r["schoolCode"] for r in recs} != tested:
            continue
        rate = weighted([(r["rate"], r["validScores"]) for r in recs])
        if rate is None:
            continue
        store.add("proficiency", {
            "year": year, "era": era_of(year), "districtCode": d, "schoolCode": None,
            "subject": subj, "test": test, "group": grp,
            "validScores": sum(r["validScores"] for r in recs), "rate": rate,
            "stateRate": next((r["stateRate"] for r in recs if r.get("stateRate") is not None), None),
            "source": f"derived: weighted mean of {len(recs)} school(s) by valid scores",
        }, (TIER_DERIVED, year))

    # 2. Grades 5-8 Math composite, weighted by valid scores.
    buckets = defaultdict(list)
    for (year, d, s, subj, test, grp), (_, r) in list(prof.items()):
        if subj == "Math" and test in ("Grade 5", "Grade 6", "Grade 7", "Grade 8"):
            buckets[(year, d, s, grp)].append(r)
    for (year, d, s, grp), recs in buckets.items():
        if any(r.get("validScores") is None for r in recs):
            continue
        rate = weighted([(r["rate"], r["validScores"]) for r in recs])
        if rate is None:
            continue
        store.add("proficiency", {
            "year": year, "era": era_of(year), "districtCode": d, "schoolCode": s,
            "subject": "Math", "test": "Grades 5-8", "group": grp,
            "validScores": sum(r["validScores"] for r in recs), "rate": rate, "stateRate": None,
            "source": "derived: Grades 5-8 weighted by valid scores",
        }, (TIER_DERIVED, year))

    # 3. Science 'All' = unweighted mean of the tested grades when no overall figure exists.
    buckets = defaultdict(list)
    for (year, d, s, subj, test, grp), (_, r) in list(prof.items()):
        if subj == "Science" and test.startswith("Grade") and r.get("assessment") == "NJSLA-S":
            buckets[(year, d, s, grp)].append(r)
    for (year, d, s, grp), recs in buckets.items():
        if (year, d, s, "Science", "All", grp) in prof:
            continue
        rates = [r["rate"] for r in recs if r["rate"] is not None]
        states = [r["stateRate"] for r in recs if r.get("stateRate") is not None]
        if not rates:
            continue
        store.add("proficiency", {
            "year": year, "era": era_of(year), "districtCode": d, "schoolCode": s,
            "subject": "Science", "test": "All", "group": grp, "validScores": None,
            "rate": round(sum(rates) / len(rates), 1),
            "stateRate": round(sum(states) / len(states), 1) if len(states) == len(rates) else None,
            "assessment": "NJSLA-S",
            "source": f"derived: mean of {', '.join(sorted(r['test'] for r in recs))}",
        }, (TIER_DERIVED, year))

    # 4. District chronic absenteeism from schools, weighted by enrollment.
    enroll = {(r["year"], r["districtCode"], r["schoolCode"]): r.get("enrollment")
              for r in store.records("staffing")}
    absn = store.data["absenteeism"]
    buckets = defaultdict(list)
    for (year, d, s, grp), (_, r) in list(absn.items()):
        if s:
            buckets[(year, d, grp)].append(r)
    for (year, d, grp), recs in buckets.items():
        if (year, d, None, grp) in absn or grp != "All Students":
            continue
        rate = weighted([(r["rate"], enroll.get((year, d, r["schoolCode"]))) for r in recs])
        if rate is None:
            continue
        store.add("absenteeism", {
            "year": year, "era": era_of(year), "districtCode": d, "schoolCode": None, "group": grp,
            "rate": rate, "stateRate": None,
            "source": f"derived: enrollment-weighted mean of {len(recs)} school(s)",
        }, (TIER_DERIVED, year))

    # 5. SAT total = Math + Evidence-Based Reading & Writing averages.
    hs = store.data["highschool"]
    for (year, d, s, metric, grp), (_, r) in list(hs.items()):
        if metric == "satMath" and (year, d, s, "satEbrw", grp) in hs:
            ebrw = hs[(year, d, s, "satEbrw", grp)][1]
            if r.get("value") is not None and ebrw.get("value") is not None:
                state = (r["stateValue"] + ebrw["stateValue"]) if r.get("stateValue") and ebrw.get("stateValue") else None
                store.add("highschool", {"year": year, "era": era_of(year), "districtCode": d, "schoolCode": s, "metric": "satTotal", "group": grp,
                                         "value": r["value"] + ebrw["value"], "stateValue": state,
                                         "source": "derived: SAT Math + Reading & Writing averages"}, (TIER_DERIVED, year))

    # 6. Staffing estimates for years that only published ratios.
    for key, (pri, r) in list(store.data["staffing"].items()):
        e = r.get("enrollment")
        if r.get("teachers") is None and e and r.get("studentTeacherRatio"):
            r["teachers"] = round(e / r["studentTeacherRatio"])
            r["teachersEstimated"] = True
        if r.get("administrators") is None and e and r.get("studentAdminRatio"):
            r["administrators"] = round(e / r["studentAdminRatio"])
            r["administratorsEstimated"] = True
        # NJDOE's 2019-20 district file swaps the student:admin and teacher:admin columns
        # (it reports 17:1). If a published ratio disagrees with the published counts by
        # more than 25%, trust the counts and flag the correction.
        for ratio_key, count_key in (("studentAdminRatio", "administrators"), ("studentTeacherRatio", "teachers")):
            ratio, count = r.get(ratio_key), r.get(count_key)
            if ratio and count and e and not r.get(f"{count_key}Estimated"):
                computed = round(e / count)
                if abs(computed - ratio) / computed > 0.25:
                    LOG.warning("%s %s: published %s %s disagrees with counts (%s); using %s",
                                r["year"], r["districtCode"], ratio_key, ratio, computed, computed)
                    r[ratio_key] = computed
                    r[f"{ratio_key}Corrected"] = True
        if r.get("teachers") and r.get("administrators"):
            r["teachersPerAdmin"] = round(r["teachers"] / r["administrators"], 1)


def build_dataset(extract_files: list[Path]) -> tuple[Store, dict]:
    store = Store()
    school_names: dict[str, str] = {}
    unmatched: dict[str, set] = defaultdict(set)
    for path in sorted(extract_files):
        ex = load_extract(path)
        file_year, kind = ex["fileYear"], ex["kind"]
        school_names.update({k: v for k, v in ex.get("schoolNames", {}).items() if v})
        for sheet, payload in ex["sheets"].items():
            ctx = Ctx(file_year, kind, sheet, payload["columns"])
            hit = False
            for extractor in EXTRACTORS:
                if extractor.matches(ctx.sheet_n):
                    hit = True
                    try:
                        extractor.fn(ctx, payload["rows"], store)
                    except Exception:  # one odd sheet must never sink the whole run
                        LOG.exception("extractor %s failed on %s %s/%s", extractor.name, file_year, kind, sheet)
            if not hit and "header" not in ctx.sheet_n:
                unmatched[file_year].add(sheet)
    for y, sheets in sorted(unmatched.items()):
        LOG.debug("%s: sheets kept but not mapped: %s", y, ", ".join(sorted(sheets)))
    derive(store)
    return store, school_names


# --------------------------------------------------------------------------- #
# Statewide benchmarks: where BRRSD ranks among every NJ district, and the
# value a district needs to be in the Top 20 (or Top 10) on each metric.
# --------------------------------------------------------------------------- #
STATEWIDE_EXTRACTORS = {"prof_summary", "prof_by_test", "science", "growth", "absenteeism", "apib", "grad", "sat"}
BENCH_GROUPS = ("All Students", "Economically Disadvantaged", "Students with Disabilities")
BENCH_METRICS = {
    # key: (label, dataset, unit, higherIsBetter)
    "ela": ("ELA proficiency", "proficiency", "pct", True),
    "math": ("Math proficiency", "proficiency", "pct", True),
    "science": ("Science proficiency", "proficiency", "pct", True),
    "algebra1": ("Algebra I proficiency", "proficiency", "pct", True),
    "math58": ("Grades 5-8 Math proficiency", "proficiency", "pct", True),
    "msgpEla": ("ELA growth (mSGP)", "growth", "sgp", True),
    "msgpMath": ("Math growth (mSGP)", "growth", "sgp", True),
    "absenteeism": ("Chronic absenteeism", "absenteeism", "pct", False),
    "elaMath": ("ELA + Math proficiency (average)", "proficiency", "pct", True),
    "grad4": ("4-year graduation rate", "highschool", "pct", True),
    "grad5": ("5-year graduation rate", "highschool", "pct", True),
    "apCourse": ("Students in AP/IB courses", "highschool", "pct", True),
    "apScore3": ("Students scoring 3+ on an AP exam", "highschool", "pct", True),
    "dualEnroll": ("Students in dual-enrollment courses", "highschool", "pct", True),
    "satTotal": ("SAT average (Math + Reading & Writing)", "highschool", "score", True),
}
# High-school measures are ranked among districts that run a high school (K-12 and
# 9-12 regionals), not only K-12 districts.
HS_METRICS = {"grad4", "grad5", "apCourse", "apScore3", "dualEnroll", "satTotal"}
BENCH_UNIVERSES = {
    "k12": "NJ K-12 districts (grades K through 12; charter and county vocational excluded); for high-school measures, every NJ district that runs a high school (K-12 and regional high-school districts)",
    "all": "All NJ districts and charters that report the metric",
}
TOP_N = 20


def statewide_sheet(title_n: str) -> bool:
    if re.search(r"header|^enrollment(trends)?bygrade$", title_n):
        return True
    return any(ex.name in STATEWIDE_EXTRACTORS and ex.matches(title_n) for ex in EXTRACTORS)


def statewide_path(year: str) -> Path:
    return STATEWIDE_DIR / f"{year}.json.gz"


def bench_metric_of(dataset: str, r: dict) -> str | None:
    if dataset == "proficiency":
        return {("ELA", "All"): "ela", ("Math", "All"): "math", ("Science", "All"): "science",
                ("Math", "Algebra I"): "algebra1", ("Math", "Grades 5-8"): "math58"}.get((r["subject"], r["test"]))
    if dataset == "growth":
        return {"ELA": "msgpEla", "Math": "msgpMath"}.get(r["subject"]) if r.get("grade") == "All" else None
    if dataset == "absenteeism":
        return "absenteeism"
    if dataset == "highschool":
        return r["metric"] if r["metric"] in HS_METRICS else None
    return None


def prev_year(year: str) -> str:
    """'2024-25' -> '2023-24'."""
    y = int(year[:4])
    return f"{y - 1}-{str(y)[2:]}"


def summarize_statewide(sheets: dict, file_year: str) -> dict:
    """Run the normal extractors over every district's rows and keep a compact table."""
    store = Store()
    districts: dict[str, dict] = defaultdict(dict)
    for sheet, payload in sheets.items():
        ctx = Ctx(file_year, "district", sheet, payload["columns"])
        if "header" in ctx.sheet_n:
            span = ctx.col([r"gradespan"])
            name = ctx.col([r"^districtname$"])
            for row in payload["rows"]:
                info = districts[row["__d"]]
                if name and row.get(name):
                    info["name"] = str(row[name])
                if span and row.get(span):
                    info["gradeSpan"] = str(row[span])
            continue
        if re.search(r"^enrollment(trends)?bygrade$", ctx.sheet_n):
            ycol = ctx.col([r"^schoolyear$"])
            name = ctx.col([r"^districtname$"])
            for row in payload["rows"]:
                if ycol and ctx.year_of(row, ycol) != file_year:
                    continue
                info = districts[row["__d"]]
                if name and row.get(name):
                    info.setdefault("name", str(row[name]))
                g = {k: num(row.get(k)) or 0 for k in ("gradekg", "grade01", "grade02", "grade03", "grade04", "grade05", "grade12", "total")}
                # Real elementary and senior classes, not a handful of special-program seats
                # (e.g. a 9-12 regional that NJDOE lists as "PK-12").
                info["elementary"] = int(sum(g[k] for k in ("gradekg", "grade01", "grade02", "grade03", "grade04", "grade05")))
                info["grade12"] = int(g["grade12"])
                if g["total"]:
                    info["enrollment"] = int(g["total"])
            continue
        for extractor in EXTRACTORS:
            if extractor.name in STATEWIDE_EXTRACTORS and extractor.matches(ctx.sheet_n):
                try:
                    extractor.fn(ctx, payload["rows"], store)
                except Exception:
                    LOG.exception("statewide extractor %s failed on %s/%s", extractor.name, file_year, sheet)
    derive(store)
    records = []
    for ds in ("proficiency", "growth", "absenteeism", "highschool"):
        for r in store.records(ds):
            if r.get("schoolCode") or r["group"] not in BENCH_GROUPS:
                continue
            m = bench_metric_of(ds, r)
            # 5-year graduation rates arrive a year late (the 2024-25 file reports the class of 2024).
            late = m == "grad5" and r["year"] == prev_year(file_year)
            if r["year"] != file_year and not late:
                continue
            value = r.get("msgp") if ds == "growth" else r.get("value") if ds == "highschool" else r.get("rate")
            if m and value is not None:
                records.append([m, r["group"], r["districtCode"], value] + ([r["year"]] if late else []))
    records.sort()
    return {"fileYear": file_year, "districts": dict(sorted(districts.items())), "records": records}


def is_k12(dkey: str, info: dict) -> bool:
    if int(dkey[:2]) >= 80:  # 80 = charter schools, 90s = special services etc.
        return False
    if "elementary" in info:
        return info["elementary"] >= 100 and info["grade12"] >= 25
    span = norm(info.get("gradeSpan", ""))
    return bool(re.fullmatch(r"(pk|kg|k|01)\w*12", span))


def is_hs(dkey: str, info: dict) -> bool:
    """A district that runs its own high school: K-12 or a 9-12 / 7-12 regional (no charters, vocational or special services)."""
    if int(dkey[:2]) >= 80 or re.search(r"vocational|technical|specialservice|special serv", norm(info.get("name", ""))):
        return False
    if "grade12" in info:
        return info["grade12"] >= 25
    return norm(info.get("gradeSpan", "")).endswith("12")


def build_benchmarks(paths: list[Path]) -> tuple[list[dict], dict]:
    out = []
    extracts = [load_extract(p) for p in sorted(paths)]
    # District facts from every year, newest winning: some years lack a usable
    # enrollment-by-grade sheet (2016-17 is in long format), and grade spans rarely change.
    k12_known: dict[str, dict] = {}
    for sw in extracts:
        for d, i in sw["districts"].items():
            if i.get("elementary") == 0 and i.get("grade12") == 0:
                i = {k: v for k, v in i.items() if k not in ("elementary", "grade12")}
            k12_known[d] = {**k12_known.get(d, {}), **i}
    top_sets: dict[str, list[dict]] = {}
    by_year: dict[str, dict[tuple, list]] = defaultdict(lambda: defaultdict(list))
    for sw in extracts:
        for m, group, d, v, *ry in sw["records"]:
            by_year[ry[0] if ry else sw["fileYear"]][(m, group)].append((d, v))
    for year in sorted(by_year):
        by_key = by_year[year]
        # Composite used to pick "Top 20 districts" for the spending comparison:
        # the simple average of ELA and Math proficiency (both must be reported).
        for (m, group), vals in list(by_key.items()):
            if m == "ela":
                math = dict(by_key.get(("math", group), []))
                by_key[("elaMath", group)] = [(d, round((v + math[d]) / 2, 1)) for d, v in vals if d in math]
        for (m, group), vals in sorted(by_key.items()):
            higher = BENCH_METRICS[m][3]
            for universe in BENCH_UNIVERSES:
                eligible = is_hs if m in HS_METRICS else is_k12
                pool = [(d, v) for d, v in vals if universe == "all" or eligible(d, k12_known.get(d, {}))]
                if len(pool) < TOP_N:
                    continue
                pool.sort(key=lambda dv: (-dv[1] if higher else dv[1], dv[0]))
                values = [v for _, v in pool]
                better = (lambda a, b: a > b) if higher else (lambda a, b: a < b)
                rec = {
                    "year": year, "metric": m, "group": group, "universe": universe, "n": len(pool),
                    "best": values[0], "top10": values[9], "top20": values[TOP_N - 1],
                    "median": values[len(values) // 2],
                    "ranks": {d: 1 + sum(better(x, v) for x in values) for d, v in pool if d in DISTRICTS},
                }
                if m == "elaMath" and group == "All Students" and universe == "k12":
                    top_sets[year] = [{"districtCode": d, "name": title_case(k12_known.get(d, {}).get("name", d)), "value": v}
                                      for d, v in pool[:TOP_N]]
                if group == "All Students":
                    rec["leaders"] = [{"districtCode": d, "name": title_case(k12_known.get(d, {}).get("name", d)), "value": v}
                                      for d, v in pool[:TOP_N]]
                out.append(rec)
    meta = {
        "topN": TOP_N,
        "metrics": {k: {"label": v[0], "dataset": v[1], "unit": v[2], "higherIsBetter": v[3], "highSchool": k in HS_METRICS}
                    for k, v in BENCH_METRICS.items()},
        "universes": BENCH_UNIVERSES,
        "years": sorted({r["year"] for r in out}),
        "notes": [
            f"top{TOP_N} = the value of the {TOP_N}th-best district that year: matching it would place a district in the Top {TOP_N}.",
            "Rank 1 = best. Ties share a rank. For chronic absenteeism, lower is better.",
            "Computed from every district-level row in NJDOE's district file for that year; suppressed values are excluded.",
            "District-level files start in 2016-17, so statewide benchmarks start then.",
            "ELA + Math proficiency is the simple average of a district's ELA and Math rates; its K-12 Top 20 defines the 'Top 20 districts' used for spending comparisons.",
        ],
    }
    return out, meta, top_sets


def top20_spending(all_spending: list[dict], top_sets: dict[str, list[dict]]) -> tuple[list[dict], dict]:
    """Median spending of the Top 20 K-12 districts (ELA + Math proficiency), per spending year.

    Each spending year uses the Top 20 from the latest test year at or before it
    (no tests 2019-20/2020-21; spending years before 2016-17 use the 2016-17 list).
    """
    if not top_sets:
        return [], {}
    test_years = sorted(top_sets)
    by_year_metric: dict[tuple, list] = defaultdict(list)
    for r in all_spending:
        if r["entity"][:2].isdigit():
            by_year_metric[(r["year"], r["metric"])].append(r)
    out, used = [], {}
    for (year, metric), rows in sorted(by_year_metric.items()):
        ty = max((y for y in test_years if y <= year), default=test_years[0])
        members = {m["districtCode"] for m in top_sets[ty]}
        rows = [r for r in rows if r["entity"] in members]
        if len(rows) < 10:
            continue
        rec = {"year": year, "entity": "TOP20_MEDIAN", "metric": metric, "n": len(rows), "topYear": ty,
               "basis": "actual" if all(r["basis"] == "actual" for r in rows) else "budgeted"}
        for f in ("value", "pct", "share"):
            vals = sorted(r[f] for r in rows if r.get(f) is not None)
            if len(vals) >= 10:
                mid = len(vals) // 2
                med = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2
                rec[f] = round(med, 1) if med < 1000 else round(med)
        if "value" in rec or "pct" in rec:
            out.append(rec)
            used[year] = ty
    return out, used


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def title_case(name: str) -> str:
    if name.isupper():
        name = name.title()
        name = re.sub(r"\b(Of|And|The)\b", lambda m: m.group(1).lower(), name)
        name = re.sub(r"\bJ F\b|\bJ\.F\.\b", "J.F.", name)
    return name.strip()


def sort_key(dataset: str):
    keys = DATASET_KEYS[dataset]
    return lambda r: tuple("" if r.get(k) is None else str(r.get(k)) for k in keys)


def finalize_records(store: Store) -> dict[str, list[dict]]:
    out = {}
    for ds in DATASET_KEYS:
        recs = []
        for r in store.records(ds):
            # Only BRRSD school-level rows are published; peer school rows were only
            # needed for the early-year district aggregates.
            if r.get("schoolCode") and r["districtCode"] != TARGET_DISTRICT:
                continue
            recs.append({k: v for k, v in r.items() if v is not None})
        out[ds] = sorted(recs, key=sort_key(ds))
    return out


def write_json(payload: dict, path: Path) -> None:
    """Pretty meta block, one record per line: readable and diff-friendly."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["{"]
    keys = list(payload)
    for i, k in enumerate(keys):
        comma = "," if i < len(keys) - 1 else ""
        v = payload[k]
        if isinstance(v, list):
            lines.append(f'  "{k}": [')
            for j, item in enumerate(v):
                c = "," if j < len(v) - 1 else ""
                lines.append("    " + json.dumps(item, sort_keys=True, ensure_ascii=False) + c)
            lines.append(f"  ]{comma}")
        else:
            body = json.dumps(v, indent=2, sort_keys=True, ensure_ascii=False).replace("\n", "\n  ")
            lines.append(f'  "{k}": {body}{comma}')
    lines.append("}")
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_csvs(records: dict[str, list[dict]], schools: dict[str, str]) -> list[str]:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for ds, recs in records.items():
        df = pd.DataFrame(recs)
        if df.empty:
            continue
        df.insert(df.columns.get_loc("districtCode") + 1, "districtName",
                  df["districtCode"].map(lambda c: DISTRICTS[c]["name"]))
        if "schoolCode" in df:
            df.insert(df.columns.get_loc("schoolCode") + 1, "schoolName",
                      [schools.get(f"{d}-{s}", "") if isinstance(s, str) else ""
                       for d, s in zip(df["districtCode"], df["schoolCode"])])
        name = f"{ds}.csv"
        df.to_csv(EXPORT_DIR / name, index=False, quoting=csv.QUOTE_MINIMAL)
        written.append(name)
    # Convenience cut most often requested by residents.
    alg = [r for r in records["proficiency"] if r.get("test") == "Algebra I"]
    if alg:
        pd.DataFrame(alg).to_csv(EXPORT_DIR / "algebra1.csv", index=False)
        written.append("algebra1.csv")
    return written


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def signature_changed(old: dict | None, new: dict) -> bool:
    if not old:
        return True
    return any(old.get(k) != new.get(k) for k in ("url", "lastModified", "contentLength", "etag"))


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--backfill", action="store_true", help="re-download and re-parse every year from 2014-15")
    p.add_argument("--years", nargs="*", help="only consider these years, e.g. 2023-24 2024-25")
    p.add_argument("--force", action="store_true", help="re-process the selected years even if unchanged")
    p.add_argument("--rebuild", action="store_true", help="offline: rebuild JSON/CSV from data/extracts only")
    p.add_argument("--download-dir", type=Path, help="keep raw .xlsx downloads here (default: temp dir, deleted)")
    p.add_argument("--skip-spending", action="store_true", help="do not check the Taxpayers' Guide to Education Spending")
    p.add_argument("--dry-run", action="store_true", help="discover and report, but download nothing")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    existing = {}
    if DATA_JSON.exists():
        try:
            existing = json.loads(DATA_JSON.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            LOG.warning("existing %s is unreadable; it will be regenerated", DATA_JSON)
    manifest: dict = existing.get("sources", {})
    tges_manifest: dict = existing.get("spendingSources", {})
    ufb_manifest: dict = existing.get("budgetSources", {})
    meta_old: dict = existing.get("meta", {})
    wanted = {norm_year(y) for y in args.years} if args.years else None
    verified_at = meta_old.get("lastVerifiedAgainstNJDOE")
    failures: list[str] = []

    if not args.rebuild:
        session = make_session()
        discovered = discover_sources(session)
        if not discovered:
            LOG.error("No NJDOE files discovered; leaving existing data untouched.")
            return 1
        verified_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        backfill = args.backfill or not manifest
        if backfill and not args.backfill:
            LOG.info("No manifest yet: running a full backfill.")

        todo = []
        for year, kinds in sorted(discovered.items()):
            if wanted and year not in wanted:
                continue
            for kind, sig in kinds.items():
                old = manifest.get(year, {}).get(kind)
                if (backfill or args.force or signature_changed(old, sig) or not extract_path(year, kind).exists()
                        or (kind == "district" and not statewide_path(year).exists())):
                    todo.append((year, kind, sig))
        LOG.info("%d file(s) to process: %s", len(todo), ", ".join(f"{y}/{k}" for y, k, _ in todo) or "none")

        if args.dry_run:
            for y, k, sig in todo:
                LOG.info("  would fetch %s (%s bytes, modified %s)", sig["url"], sig["contentLength"], sig["lastModified"])
            return 0

        tmpdir = None
        dl_dir = args.download_dir
        if dl_dir is None:
            tmpdir = tempfile.mkdtemp(prefix="njdoe_")
            dl_dir = Path(tmpdir)
        dl_dir.mkdir(parents=True, exist_ok=True)
        try:
            for year, kind, sig in todo:
                LOG.info("Processing %s %s", year, kind)
                try:
                    path = download(session, sig, dl_dir / f"{year}_{kind}.xlsx")
                    statewide = {} if kind == "district" else None
                    save_extract(extract_workbook(path, kind, year, statewide), year, kind)
                    if statewide:
                        STATEWIDE_DIR.mkdir(parents=True, exist_ok=True)
                        tges.save(summarize_statewide(statewide, year), statewide_path(year))
                    manifest.setdefault(year, {})[kind] = sig | {
                        "processedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
                    if tmpdir:
                        path.unlink(missing_ok=True)
                except Exception as exc:  # keep going; the year retries next run
                    LOG.exception("Failed on %s %s: %s", year, kind, exc)
                    failures.append(f"{year}/{kind}")
        finally:
            if tmpdir:
                shutil.rmtree(tmpdir, ignore_errors=True)

        # Spending: NJDOE Taxpayers' Guide to Education Spending (one edition per year).
        if not args.skip_spending:
            peer_codes = {c[3:] for c in DISTRICTS}
            for edition, info in sorted(tges.discover(session, probe).items()):
                old = tges_manifest.get(edition, {})
                path = TGES_EXTRACT_DIR / f"{edition}.json.gz"
                changed = any(signature_changed(old.get("files", {}).get(n), sig) for n, sig in info["files"].items())
                if not (backfill or args.force or changed or not path.exists()):
                    continue
                LOG.info("Processing TGES %s edition (%s)", edition, info["kind"])
                try:
                    tges.save(tges.extract_edition(session, edition, info, peer_codes), path)
                    tges_manifest[edition] = info | {
                        "processedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
                except Exception as exc:
                    LOG.exception("Failed on TGES %s: %s", edition, exc)
                    failures.append(f"tges/{edition}")

            # Budgets: NJDOE User-Friendly Budget summaries (one edition per budget year).
            for edition, info in sorted(ufb.discover(session, probe).items()):
                old = ufb_manifest.get(edition, {})
                path = UFB_EXTRACT_DIR / f"{edition}.json.gz"
                changed = any(signature_changed(old.get("files", {}).get(k), sig) for k, sig in info["files"].items())
                if not (backfill or args.force or changed or not path.exists()):
                    continue
                LOG.info("Processing UFB %s budget", edition)
                try:
                    tges.save(ufb.extract_edition(session, edition, info, set(DISTRICTS)), path)
                    ufb_manifest[edition] = info | {
                        "processedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
                except Exception as exc:
                    LOG.exception("Failed on UFB %s: %s", edition, exc)
                    failures.append(f"ufb/{edition}")

    extract_files = sorted(EXTRACT_DIR.glob("*.json.gz"))
    benchmarks, bench_meta, top_sets = build_benchmarks(sorted(STATEWIDE_DIR.glob("*.json.gz")))
    if not extract_files:
        LOG.error("No extracts in %s; nothing to build.", EXTRACT_DIR)
        return 1
    store, school_names = build_dataset(extract_files)
    records = finalize_records(store)

    # The guide extracts hold every NJ district: keep ours, the averages, and the
    # median of the Top 20 achieving districts.
    all_spending, all_groups = tges.normalize(
        [tges.load(p) for p in sorted(TGES_EXTRACT_DIR.glob("*.json.gz"))], None)
    district_groups = {d: g for d, g in all_groups.items() if d in DISTRICTS}
    groups = set(district_groups.values())
    top20_spend, top20_years = top20_spending(all_spending, top_sets)
    spending = [r for r in all_spending if r["entity"] in DISTRICTS or (
        not r["entity"][:2].isdigit() and (not r["entity"].startswith("GROUP_") or r["entity"].split(":")[1] in groups))]
    spending += top20_spend
    ufb_spending, budget = ufb.normalize([tges.load(p) for p in sorted(UFB_EXTRACT_DIR.glob("*.json.gz"))])
    spending = ufb.merge_spending(spending, ufb_spending)

    brrsd_schools = {k: title_case(v) for k, v in sorted(school_names.items()) if k.startswith(TARGET_DISTRICT)}
    years = sorted({r["year"] for ds in records.values() for r in ds})
    meta = {
        "title": "Bridgewater-Raritan Regional School District: NJDOE School Performance Report data",
        "targetDistrict": TARGET_DISTRICT,
        "districts": DISTRICTS,
        "schools": brrsd_schools,
        "years": years,
        "latestYear": years[-1] if years else None,
        "eras": {
            "PARCC": "2014-15 to 2018-19 (PARCC; renamed NJSLA in 2018-19, same blueprint)",
            "COVID_WAIVER": "2019-20 and 2020-21 (statewide testing cancelled/waived)",
            "NJSLA": "2021-22 to present",
        },
        "statewideMsgpMedian": STATE_MSGP_MEDIAN,
        "lastVerifiedAgainstNJDOE": verified_at,
        "source": {"landing": LANDING_URL, "archive": ARCHIVE_URL},
        "notes": [
            "Proficiency = percent of valid test takers who Met or Exceeded Expectations (Level 4+ for ELA/Math, Level 3+ for Science).",
            "2014-15 and 2015-16 district figures are weighted from school-level rows (NJDOE published no district table those years).",
            "Rows with 'derived' in source were computed by this pipeline; all others are copied from NJDOE files.",
            "Suppressed NJDOE values ('*', 'N', '<10') are omitted rather than guessed.",
        ],
        "counts": {ds: len(v) for ds, v in records.items()} | {"spending": len(spending), "budget": len(budget), "benchmarks": len(benchmarks)},
        "budget": {
            "source": ufb.INDEX_URL,
            "fields": ufb.BUDGET_FIELDS,
            "years": sorted({r["year"] for r in budget}),
            "notes": [
                "From NJDOE's User-Friendly Budget summaries. Each budget year is shown as the proposed budget until its revised budget and then its audited actuals are published (records carry basis and stage).",
                "Dollar amounts are whole-district totals; per-pupil spending comparisons use the budgetary-cost figures in the spending dataset.",
                "Enrollment is full-time on-roll students including full-time special education, as advertised in the budget.",
            ],
        },
        "benchmarks": bench_meta,
        "spending": {
            "source": tges.GUIDE_URL,
            "metrics": tges.indicator_meta(),
            "districtGroups": district_groups,
            "groupLabels": {"G": "K-12 districts, 3,501+ students", "H": "Grades 7-12 / 9-12 districts",
                            "F": "K-12 districts, 1,801-3,500 students"},
            "years": sorted({r["year"] for r in spending}),
            "latestActualYear": max((r["year"] for r in spending if r["basis"] == "actual" and r["metric"] == "budgetaryCost"
                                     and r["entity"] == TARGET_DISTRICT), default=None),
            "budgetSource": ufb.INDEX_URL,
            "top20": {
                "entity": "TOP20_MEDIAN",
                "label": "Top 20 districts (median)",
                "definition": "Median of the 20 NJ K-12 districts with the highest average of ELA and Math proficiency, using the latest test year at or before each spending year.",
                "testYearUsed": top20_years,
                "members": top_sets,
            },
            "notes": [
                "Budgetary cost per pupil (TGES Indicator 1) allocates employee benefits into each category and excludes transportation, out-of-district tuition, capital outlay, debt service and state-paid TPAF pensions, so districts are comparable.",
                "pct = share of budgetary cost per pupil; share = share of the parent category (e.g., salaries & benefits as % of classroom instruction).",
                "The newest year in each TGES edition is a budget, not an actual; records carry basis='budgeted'.",
                "Total spending per pupil includes every fund and is not comparable to budgetary cost.",
                "Years after the latest Taxpayers' Guide come from the User-Friendly Budget per-pupil cost tables (source='ufb'): same indicators, but no state or group averages or ranks.",
            ],
        },
    }
    payload = {"schemaVersion": SCHEMA_VERSION, "meta": meta,
               "sources": {y: manifest[y] for y in sorted(manifest)},
               "spendingSources": {e: tges_manifest[e] for e in sorted(tges_manifest)},
               "budgetSources": {e: ufb_manifest[e] for e in sorted(ufb_manifest)}} | records | {
                   "spending": spending, "budget": budget, "benchmarks": benchmarks}

    write_json(payload, DATA_JSON)
    exports = write_csvs(records, brrsd_schools | {k: title_case(v) for k, v in school_names.items()})
    if spending:
        names = {c: d["name"] for c, d in DISTRICTS.items()} | {
            tges.STATE_AVG: "Statewide average", tges.STATE_MEDIAN: "Statewide median",
            "TOP20_MEDIAN": "Top 20 districts (median)"}
        labels = meta["spending"]["metrics"]
        df = pd.DataFrame(spending)
        df.insert(2, "entityName", df["entity"].map(
            lambda e: names.get(e) or f"Peer group {e.split(':')[1]} {'average' if 'AVG' in e else 'median'}"))
        df.insert(4, "metricLabel", df["metric"].map(lambda m: labels[m]["label"]))
        df.to_csv(EXPORT_DIR / "spending.csv", index=False)
        exports.append("spending.csv")
    if budget:
        bdf = pd.DataFrame([{k: v for k, v in r.items() if k != "taxRates"} for r in budget])
        bdf.insert(2, "districtName", bdf["districtCode"].map(lambda c: DISTRICTS[c]["name"]))
        bdf.to_csv(EXPORT_DIR / "budget.csv", index=False)
        exports.append("budget.csv")
    if benchmarks:
        rows = []
        for b in benchmarks:
            row = {k: v for k, v in b.items() if k not in ("ranks", "leaders")}
            row["metricLabel"] = bench_meta["metrics"][b["metric"]]["label"]
            for d, info in DISTRICTS.items():
                row[f"rank {info['short']}"] = b["ranks"].get(d)
            rows.append(row)
        pd.DataFrame(rows).to_csv(EXPORT_DIR / "benchmarks.csv", index=False)
        exports.append("benchmarks.csv")
    LOG.info("Wrote %s (%s) and %d CSV exports", DATA_JSON.relative_to(ROOT),
             ", ".join(f"{k}={v}" for k, v in meta["counts"].items()), len(exports))
    if failures:
        LOG.warning("Completed with failures (will retry next run): %s", ", ".join(failures))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
NJDOE Taxpayers' Guide to Education Spending (TGES): discovery, extraction, normalization.

TGES is NJDOE's audited, apples-to-apples spending comparison. Every edition reports, for
every district, "budgetary cost per pupil" split into Classroom Instruction, Support
Services, Administration, Operations & Maintenance and Extracurricular, each as dollars
per pupil AND as a % of budgetary cost, with employee benefits allocated into each
category. It also publishes a statewide average/median row and a row for each
enrollment-size peer group (BRRSD is "G. K-12 / 3501+"). Budgetary cost excludes
transportation, out-of-district tuition, capital, debt service and state-paid TPAF
pensions, which is what makes districts comparable.

Two physical layouts exist and both are handled:
  * 2017-2023 editions: docs/<YYYY>_TGES.zip containing CSG<n>.CSV files with coded
    headers (PP1x/RK1x/PCT1x = year 1 of 3, SBA3 = share of parent, STRAT00 = ratio...).
  * 2024+ editions: <YYYY>tges.shtml linking docs/<YYYY>/CSG<n>.xlsx with readable
    headers ("2023-24 Actual Per Pupil Amount", "% of Budgetary Cost per Pupil").

Edition E covers years (E-3)-(E-2) actual, (E-2)-(E-1) actual, (E-1)-E budgeted.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import logging
import re
import zipfile
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin

import openpyxl
import requests
from bs4 import BeautifulSoup

LOG = logging.getLogger("njdoe_etl.tges")

GUIDE_URL = "https://www.nj.gov/education/guide/"
FIRST_EDITION = 2017  # its first year is 2014-15, matching the performance data

# Indicator file -> stable key, label, unit. Ratio/salary files yield two metrics.
INDICATORS = {
    "1": ("budgetaryCost", "Budgetary cost per pupil", "usd"),
    "2": ("classroom", "Classroom instruction", "usd"),
    "3": ("classroomSalBen", "Classroom salaries & benefits", "usd"),
    "4": ("classroomSupplies", "Classroom supplies & textbooks", "usd"),
    "5": ("classroomPurchased", "Classroom purchased services", "usd"),
    "6": ("support", "Support services", "usd"),
    "7": ("supportSalBen", "Support services salaries & benefits", "usd"),
    "8": ("admin", "Administration", "usd"),
    "8A": ("legal", "Legal services", "usd"),
    "9": ("adminSalBen", "Administration salaries & benefits", "usd"),
    "10": ("operations", "Operations & maintenance of plant", "usd"),
    "11": ("operationsSalBen", "O&M salaries & benefits", "usd"),
    "12": ("foodService", "Board contribution to food service", "usd"),
    "13": ("extracurricular", "Extracurricular", "usd"),
    "14": ("benefitsPctSalaries", "Employee benefits as % of salaries", "pct"),
    "15": ("equipment", "Equipment", "usd"),
    "16": (("studentTeacherRatio", "medianTeacherSalary"), ("Students per classroom teacher", "Median classroom teacher salary"), ("ratio", "usd")),
    "17": (("studentSupportRatio", "medianSupportSalary"), ("Students per support staff", "Median support staff salary"), ("ratio", "usd")),
    "18": (("studentAdminRatio", "medianAdminSalary"), ("Students per administrator", "Median administrator salary"), ("ratio", "usd")),
    "19": ("facultyAdminRatio", "Faculty per administrator", "ratio"),
    "TOTAL": ("totalSpending", "Total spending per pupil (all funds)", "usd"),
}
WANTED_FILES = {f"csg{k.lower()}" for k in INDICATORS if k != "TOTAL"} | {"csg1aaavgs", "totalspending"}

STATE_AVG, STATE_MEDIAN = "STATE_AVG", "STATE_MEDIAN"


def _norm(v) -> str:
    return re.sub(r"[^a-z0-9%]", "", str(v or "").lower())


def _year(start: int) -> str:
    return f"{start}-{str(start + 1)[2:]}"


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("$", "").replace("%", "")
    try:
        return float(s)
    except ValueError:
        return None


def indicator_meta() -> dict:
    """Flat {key: {label, unit, file}} for the frontend."""
    out = {}
    for f, (key, label, unit) in INDICATORS.items():
        if isinstance(key, tuple):
            for k, l, u in zip(key, label, unit):
                out[k] = {"label": l, "unit": u, "indicator": f}
        else:
            out[key] = {"label": label, "unit": unit, "indicator": f}
    return out


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def discover(session: requests.Session, probe) -> dict[str, dict]:
    """Return {edition: {"kind": "zip"|"xlsx", "files": {name: signature}}}."""
    try:
        resp = session.get(GUIDE_URL, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as exc:
        LOG.warning("Could not read the TGES index (%s)", exc)
        return {}
    soup = BeautifulSoup(resp.text, "html.parser")
    zips, pages = {}, {}
    for a in soup.find_all("a", href=True):
        href = urljoin(GUIDE_URL, a["href"])
        m = re.search(r"/(20\d\d)_TGES\.zip$", href, re.I)
        if m:
            zips[m.group(1)] = href
        m = re.search(r"/(20\d\d)tges\.shtml$", href, re.I)
        if m:
            pages[m.group(1)] = href

    found: dict[str, dict] = {}
    for ed, url in sorted(pages.items()):
        if int(ed) < FIRST_EDITION:
            continue
        try:
            psoup = BeautifulSoup(session.get(url, timeout=60).text, "html.parser")
        except requests.RequestException as exc:
            LOG.warning("TGES %s page unreadable: %s", ed, exc)
            continue
        files = {}
        for a in psoup.find_all("a", href=True):
            href = urljoin(url, a["href"])
            name = href.rsplit("/", 1)[-1]
            if re.search(r"\.xlsx$", name, re.I) and _norm(name.rsplit(".", 1)[0]) in WANTED_FILES:
                sig = probe(session, href)
                if sig:
                    files[name] = sig
        if files:
            found[ed] = {"kind": "xlsx", "files": files}
    for ed, url in sorted(zips.items()):
        if int(ed) < FIRST_EDITION or ed in found:
            continue
        sig = probe(session, url)
        if sig:
            found[ed] = {"kind": "zip", "files": {url.rsplit("/", 1)[-1]: sig}}
    LOG.info("Discovered TGES editions: %s", ", ".join(f"{e}[{v['kind']}]" for e, v in sorted(found.items())) or "none")
    return found


# --------------------------------------------------------------------------- #
# Extraction (keeps only our districts + state/group comparison rows)
# --------------------------------------------------------------------------- #
def _table_rows(header, rows):
    return [dict(zip(header, r)) for r in rows]


def _read_csv(data: bytes):
    text = data.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return [], []
    return [h.strip() for h in rows[0]], rows[1:]


def _read_xlsx(data: bytes):
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    header, body = None, []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if header is None:
            if any(_norm(c) in ("districtname", "distname") for c in row):
                header = [str(c).strip() if c is not None else "" for c in row]
            elif i > 15:
                break
            continue
        body.append(list(row))
    wb.close()
    return header or [], body


# NJ county codes are assigned alphabetically; the guide lists counties by name only.
COUNTY_CODES = {
    "atlantic": "01", "bergen": "03", "burlington": "05", "camden": "07", "capemay": "09", "cumberland": "11",
    "essex": "13", "gloucester": "15", "hudson": "17", "hunterdon": "19", "mercer": "21", "middlesex": "23",
    "monmouth": "25", "morris": "27", "ocean": "29", "passaic": "31", "salem": "33", "somerset": "35",
    "sussex": "37", "union": "39", "warren": "41",
}


def _district_key(row: dict) -> str | None:
    """'35-0555' from a guide row's county name and district number, or None."""
    dist = next((v for k, v in row.items() if _norm(k) in ("districtcode", "dist")), None)
    county = _norm(next((v for k, v in row.items() if _norm(k) in ("county", "coname")), ""))
    county = re.sub(r"county$", "", county)
    try:
        d = f"{int(float(str(dist).strip())):04d}"
    except (TypeError, ValueError):
        return None
    cc = COUNTY_CODES.get(county)
    return f"{cc}-{d}" if cc else None


def _keep(row: dict, codes: set[str]) -> bool:
    """Keep every district row (statewide comparisons) plus the average/median rows."""
    name = _norm(next((v for k, v in row.items() if _norm(k) in ("districtname", "distname")), ""))
    if "average" in name or "median" in name:
        return True
    return _district_key(row) is not None


def extract_edition(session, edition: str, info: dict, codes: set[str]) -> dict:
    """Download one edition and keep only the rows we need. Returns a small dict."""
    tables = {}
    blobs: list[tuple[str, bytes]] = []
    for name, sig in info["files"].items():
        r = session.get(sig["url"], timeout=(30, 300))
        r.raise_for_status()
        if info["kind"] == "zip":
            with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
                for member in zf.namelist():
                    base = member.rsplit("/", 1)[-1]
                    if _norm(base.rsplit(".", 1)[0]) in WANTED_FILES and base.lower().endswith((".csv", ".xlsx")):
                        blobs.append((base, zf.read(member)))
        else:
            blobs.append((name, r.content))
    for base, data in blobs:
        stem = _norm(base.rsplit(".", 1)[0])
        header, body = _read_csv(data) if base.lower().endswith(".csv") else _read_xlsx(data)
        if not header:
            LOG.warning("TGES %s %s: no header row found", edition, base)
            continue
        rows = [r for r in _table_rows(header, body) if _keep(r, codes)]
        tables[stem] = {"header": header, "rows": [{k: v for k, v in r.items() if k} for r in rows]}
    LOG.info("  TGES %s: kept %d tables", edition, len(tables))
    return {"edition": edition, "kind": info["kind"], "tables": tables}


def save(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, sort_keys=True, default=str, separators=(",", ":")).encode()
    with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(payload)


def load(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #
def _classify_column(col: str, edition: int, stem: str):
    """Map a header to (year, field, basis) or None. Handles coded and readable headers."""
    raw = col.strip()
    n = _norm(raw)
    # Readable (2024+) headers carry the year explicitly.
    m = re.search(r"(20\d\d)\s*[–\-]\s*(\d\d)", raw)
    if m:
        year = _year(int(m.group(1)))
        # The newest year of the three-year spending tables is a budget. Staff-ratio tables
        # (csg16-19) and total spending only carry actuals. ("% of Budgetary Cost" says nothing
        # about basis, so the header text can't be used.)
        two_year = stem in ("csg16", "csg17", "csg18", "csg19", "csg1aaavgs", "totalspending")
        basis = "budgeted" if int(m.group(1)) == edition - 1 and not two_year else "actual"
        if "rank" in n:
            return year, ("rankSalary" if "salary" in n else "rank"), basis
        if "amountasa%" in n:
            return year, "share", basis
        if stem == "csg14":
            return year, "value", basis  # benefits as % of salaries (mislabelled by NJDOE)
        if "%ofbudgetary" in n:
            return year, "pct", basis
        if "salary" in n:
            return year, "salary", basis
        if "ratio" in n or "perpupil" in n or "costperpupil" in n:
            return year, "value", basis
        if "enrollment" in n:
            return year, "enrollment", basis
        if "totalspending" in n or "coststotal" in n:
            return year, "total", basis
        return None
    up = raw.upper()
    # Coded three-year columns: PP1x, RK1x, PCT1x, SBA3...
    m = re.fullmatch(r"(PP|RK|PCT)([123])(\d+[A-Z]?)", up)
    if m and not m.group(3).endswith("A"):
        i = int(m.group(2))
        return _year(edition - 4 + i), {"PP": "value", "RK": "rank", "PCT": "pct"}[m.group(1)], ("budgeted" if i == 3 else "actual")
    m = re.fullmatch(r"SB([ABC])\d+", up)
    if m:
        i = "ABC".index(m.group(1)) + 1
        return _year(edition - 4 + i), "share", ("budgeted" if i == 3 else "actual")
    # Coded two-year ratio columns: STRAT0016 / RK0116 / SALT0016 / RKSAL0116 ...
    m = re.fullmatch(r"(STRAT|SSRAT|SARAT|FARAT|RKSAL|RK|SALT|SALS|SALAM)0([01])\d+", up)
    if m:
        year = _year(edition - 2 + int(m.group(2)))
        field = {"RKSAL": "rankSalary", "RK": "rank", "SALT": "salary", "SALS": "salary", "SALAM": "salary"}.get(m.group(1), "value")
        return year, field, "actual"
    # Total spending (CSG1AA_AVGS): EXP11A, ADE11A, PP11A, RK11A
    m = re.fullmatch(r"(EXP|ADE|PP|RK)([12])1A", up)
    if m:
        year = _year(edition - 4 + int(m.group(2)))
        return year, {"EXP": "total", "ADE": "enrollment", "PP": "value", "RK": "rank"}[m.group(1)], "actual"
    return None


def _entity(row: dict, peer_codes: set[str]):
    name = str(next((v for k, v in row.items() if _norm(k) in ("districtname", "distname")), "")).lower()
    group = str(next((v for k, v in row.items() if _norm(k) == "group" or _norm(k).startswith("operatingtype")), "") or "")
    gkey = group.strip()[:1].upper() if group else None
    if "all operating" in name:
        return (STATE_AVG if "average" in name else STATE_MEDIAN), None
    if "average" in name or "median" in name:
        if not gkey:
            return None, None
        return (f"GROUP_AVG:{gkey}" if "average" in name else f"GROUP_MEDIAN:{gkey}"), gkey
    code = _district_key(row)
    if not code:
        return None, None
    return (code if peer_codes is None or code[3:] in peer_codes else None), gkey


def normalize(extracts: list[dict], peer_codes: set[str]) -> tuple[list[dict], dict]:
    best: dict[tuple, tuple] = {}
    district_groups: dict[str, str] = {}
    for ex in sorted(extracts, key=lambda e: e["edition"]):
        edition = int(ex["edition"])
        for stem, table in ex["tables"].items():
            ind = "TOTAL" if stem in ("csg1aaavgs", "totalspending") else stem[3:].upper()
            if ind not in INDICATORS:
                continue
            key, _, _ = INDICATORS[ind]
            colmap = {c: _classify_column(c, edition, stem) for c in table["header"] if c}
            for row in table["rows"]:
                entity, gkey = _entity(row, peer_codes)
                if not entity:
                    continue
                if entity[:2].isdigit() and gkey:
                    district_groups[entity] = gkey
                per_year: dict[tuple, dict] = defaultdict(dict)
                for col, val in row.items():
                    spec = colmap.get(col)
                    if not spec:
                        continue
                    year, field, basis = spec
                    v = _num(val) if field not in ("rank", "rankSalary") else (str(val).strip() if val not in (None, "", "N.A.") else None)
                    if v is not None:
                        per_year[(year, basis)][field] = v
                for (year, basis), fields in per_year.items():
                    metrics = []
                    if isinstance(key, tuple):  # ratio + salary pair
                        if "value" in fields:
                            metrics.append((key[0], {"value": fields["value"], "rank": fields.get("rank")}))
                        if "salary" in fields:
                            metrics.append((key[1], {"value": fields["salary"], "rank": fields.get("rankSalary")}))
                    else:
                        f = {k: fields.get(k) for k in ("value", "pct", "share", "rank", "total", "enrollment")}
                        if ind == "14" and f["value"] is not None and f["value"] <= 1:
                            f["value"] = f["value"] * 100  # store as percent
                        metrics.append((key, f))
                    for mkey, f in metrics:
                        if f.get("value") is None and f.get("pct") is None:
                            continue
                        rec = {"year": year, "entity": entity, "metric": mkey, "basis": basis, "edition": edition}
                        for k, v in f.items():
                            if v is None:
                                continue
                            if k in ("pct", "share"):
                                v = round(v * 100, 1) if v <= 1.5 else round(v, 1)
                            elif isinstance(v, float):
                                v = round(v, 1) if v < 1000 else round(v)
                            rec[k] = v
                        pri = (1 if basis == "actual" else 0, edition)
                        k2 = (year, entity, mkey)
                        if k2 not in best or pri > best[k2][0]:
                            best[k2] = (pri, rec)
    # Keep only the peer groups our districts belong to.
    if peer_codes is not None:
        district_groups = {d: g for d, g in district_groups.items() if d[3:] in peer_codes}
    groups = set(district_groups.values())
    records = [r for _, r in best.values()
               if not r["entity"].startswith("GROUP_") or r["entity"].split(":")[1] in groups]
    records.sort(key=lambda r: (r["metric"], r["entity"], r["year"]))
    return records, district_groups

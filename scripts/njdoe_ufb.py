"""NJDOE User-Friendly Budget (UFB) summaries: discovery, extraction, normalization.

Every spring each NJ district files a "user-friendly" budget summary with NJDOE,
which publishes statewide CSVs at https://www.nj.gov/education/budget/ufb/.
Edition E (e.g. 2027 = the 2026-27 budget) contains:

  approp / rev / enroll  amount_1 = (E-2)-(E-1) actual (audited)
                         amount_2 = (E-1)-E   revised budget
                         amount_3 = E-(E+1)   proposed budget  (school year E-1..E)
  pupcst                 year_1 = (E-3) actual, year_2 = (E-2) actual,
                         year_3 = (E-1) original budget, year_4 = (E-1) revised budget,
                         year_5 = E proposed budget
  esttax                 estimated school tax rates for the proposed year

The per-pupil lines in pupcst are the same "budgetary comparative per pupil cost"
indicators as NJDOE's Taxpayers' Guide to Education Spending, so they extend that
series one to two years further (without state or group averages).

Files are named inconsistently across years (approp27.csv, approp25_minus_1.csv ...),
so they are discovered from each year's index page.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from collections import defaultdict
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

LOG = logging.getLogger("njdoe_etl.ufb")

INDEX_URL = "https://www.nj.gov/education/budget/ufb/"
KINDS = ("pupcst", "approp", "rev", "enroll", "esttax")


def _norm(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _year(start: int) -> str:
    return f"{start}-{str(start + 1)[2:]}"


def _num(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("$", "").replace("%", "")
    try:
        return float(s)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def discover(session: requests.Session, probe) -> dict[str, dict]:
    """Return {edition: {"page": url, "files": {kind: signature}}}; edition '2027' = 2026-27 budget."""
    try:
        resp = session.get(INDEX_URL, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as exc:
        LOG.warning("Could not read the UFB index (%s)", exc)
        return {}
    pages = {}
    for a in BeautifulSoup(resp.text, "html.parser").find_all("a", href=True):
        m = re.search(r"/ufb/(\d\d)(\d\d)/(index\.shtml)?$", a["href"])
        if m:
            pages[str(2000 + int(m.group(2)))] = urljoin(INDEX_URL, a["href"])
    found = {}
    for edition, page in sorted(pages.items()):
        try:
            soup = BeautifulSoup(session.get(page, timeout=60).text, "html.parser")
        except requests.RequestException as exc:
            LOG.warning("UFB %s page unreadable: %s", edition, exc)
            continue
        files = {}
        for a in soup.find_all("a", href=True):
            href = urljoin(page, a["href"])
            name = href.rsplit("/", 1)[-1].lower()
            m = re.match(r"(pupcst|approp|rev|enroll|esttax)\d", name)
            if m and name.endswith(".csv") and m.group(1) not in files:
                sig = probe(session, href)
                if sig:
                    files[m.group(1)] = sig
        if files:
            found[edition] = {"page": page, "files": files}
    LOG.info("Discovered UFB editions: %s", ", ".join(sorted(found)) or "none")
    return found


# --------------------------------------------------------------------------- #
# Extraction: keep only our districts' rows
# --------------------------------------------------------------------------- #
def extract_edition(session: requests.Session, edition: str, info: dict, district_keys: set[str]) -> dict:
    """district_keys like {'35-0555', ...}."""
    tables = {}
    for kind, sig in info["files"].items():
        resp = session.get(sig["url"], timeout=180)
        resp.raise_for_status()
        text = resp.content.decode("cp1252", errors="replace")
        rows = []
        for row in csv.DictReader(io.StringIO(text)):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            cc, dc = row.get("county_id", ""), row.get("district_id", "")
            key = f"{cc.zfill(2)}-{dc.zfill(4)}" if cc.isdigit() and dc.isdigit() else None
            if key in district_keys:
                row["__d"] = key
                rows.append(row)
        tables[kind] = rows
    LOG.info("  UFB %s: %s", edition, ", ".join(f"{k}={len(v)}" for k, v in sorted(tables.items())))
    return {"edition": int(edition), "tables": tables}


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #
# Per-pupil lines -> the Taxpayers' Guide metric keys used by njdoe_tges.
PER_PUPIL = {
    "totalbudgetarycomparativeperpupilcost": "budgetaryCost",
    "totalclassroominstruction": "classroom",
    "classroomsalariesandbenefits": "classroomSalBen",
    "classroomgeneralsuppliesandtextbooks": "classroomSupplies",
    "classroompurchasedservices": "classroomPurchased",
    "totalsupportservices": "support",
    "supportservicessalariesandbenefits": "supportSalBen",
    "totaladministrativecosts": "admin",
    "administrationsalariesandbenefits": "adminSalBen",
    "totaloperationsandmaintenanceofplant": "operations",
    "operationsandmaintenancesalariesandbenefits": "operationsSalBen",
    "boardcontributiontofoodservices": "foodService",
    "totalextracurricularcosts": "extracurricular",
    "totalequipmentcosts": "equipment",
    "legalcosts": "legal",
    "employeebenefitsasapercentageofsalaries": "benefitsPctSalaries",
}
PCT_OF_BUDGETARY = {"classroom", "classroomSalBen", "classroomSupplies", "classroomPurchased", "support", "supportSalBen",
                    "admin", "adminSalBen", "operations", "operationsSalBen", "foodService", "extracurricular", "legal"}
SHARE_PARENT = {"classroomSalBen": "classroom", "supportSalBen": "support", "adminSalBen": "admin", "operationsSalBen": "operations"}

# Budget lines (dollars). First matching row (by line number) wins, so general-fund
# totals are taken before the special-revenue and debt-service sections repeat them.
APPROP_LINES = {
    "totalAppropriations": r"^totalexpendituresappropriations$",
    "generalFundTotal": r"^generalfundgrandtotal$",
    "regularInstruction": r"^totalregularprogramsinstruction$",
    "specialEdInstruction": r"^totalspecialeducationinstruction$",
    "basicSkillsInstruction": r"^totalbasicskillsremedialinstruction$",
    "bilingualInstruction": r"^totalbilingualeducationinstruction$",
    "cocurricular": r"^totalschoolsponsoredcocurricular",
    "athletics": r"^totalschoolsponsoredathletics",
    "tuition": r"^totalundistributedexpendituresinstructiontuition$",
    "healthServices": r"^totalundistributedexpenditureshealthservices$",
    "relatedServices": r"^totalundistributedexpendituresspeechotpt",
    "guidance": r"^totalundistributedexpendituresguidance$",
    "childStudyTeams": r"^totalundistributedexpenditureschildstudyteams$",
    "improvementOfInstruction": r"^totalundistributedexpendituresimprovementofinstruction",
    "libraryMedia": r"^totalundistributedexpenditureseducationmediaserviceslibrary$",
    "staffTraining": r"^totalundistributedexpendituresinstructionalstafftraining",
    "generalAdministration": r"^totalundistributedexpenditures(supportservices)?generaladministration$",
    "schoolAdministration": r"^totalundistributedexpendituressupportservicesschooladministration$",
    "centralServices": r"^totalundistributedexpenditurescentralservices$",
    "adminIT": r"^totalundistributedexpendituresadministrativeinformationtechnology$",
    "plantOperations": r"^totalundistributedexpendituresoperationandmaintenanceofplant",
    "transportation": r"^totalundistributedexpendituresstudenttransportation",
    "employeeBenefits": r"^totalpersonalservicesemployeebenefits$",
    "equipment": r"^totalequipment$",
    "capitalOutlay": r"^totalcapitaloutlay$",
    "specialRevenue": r"^totalspecialrevenuefunds$",
    "debtService": r"^totaldebtservicefunds$",
}
REV_LINES = {
    "totalOperatingBudget": r"^totaloperatingbudget$",
    # "Total Tax Levy" from 2026 on; earlier editions have a single "Local Tax Levy" line.
    # Line numbers sort the general fund first, before the debt-service levy.
    "generalFundTaxLevy": r"^totaltaxlevy$|^localtaxlevy$",
    "stateAid": r"^totalrevenuesfromstatesources$",
    "federalAid": r"^totalrevenuesfromfederalsources$",
    "localRevenue": r"^totalrevenuesfromlocalsources$",
}
BUDGET_FIELDS = {
    "totalOperatingBudget": "General-fund operating budget",
    "totalAppropriations": "Total appropriations, all funds",
    "generalFundTaxLevy": "General-fund school tax levy",
    "stateAid": "State aid (general fund)",
    "federalAid": "Federal aid (general fund)",
    "localRevenue": "Local revenue (general fund)",
    "employeeBenefits": "Employee benefits",
    "regularInstruction": "Regular programs instruction",
    "specialEdInstruction": "Special education instruction",
    "basicSkillsInstruction": "Basic skills / remedial instruction",
    "bilingualInstruction": "Bilingual instruction",
    "cocurricular": "Co-curricular & extracurricular",
    "athletics": "Athletics",
    "tuition": "Out-of-district tuition",
    "healthServices": "Health services",
    "relatedServices": "Speech, OT, PT & related services",
    "guidance": "Guidance",
    "childStudyTeams": "Child study teams",
    "improvementOfInstruction": "Improvement of instruction",
    "libraryMedia": "Library & media services",
    "staffTraining": "Instructional staff training",
    "generalAdministration": "General administration",
    "schoolAdministration": "School administration",
    "centralServices": "Central services",
    "adminIT": "Administrative IT",
    "plantOperations": "Operation & maintenance of plant",
    "transportation": "Student transportation",
    "equipment": "Equipment",
    "capitalOutlay": "Capital outlay",
    "specialRevenue": "Special revenue (grants)",
    "debtService": "Debt service",
    "enrollmentOnRoll": "On-roll enrollment (full-time, incl. special education)",
}


def _line_key(row):
    try:
        return int(float(row.get("line_no") or row.get("enroll_line_no") or 0))
    except ValueError:
        return 0


def _pick(rows, patterns, amount_col):
    out = {}
    for row in sorted(rows, key=_line_key):
        n = _norm(row.get("line_desc"))
        for field, rx in patterns.items():
            if field not in out and re.search(rx, n):
                v = _num(row.get(amount_col))
                if v is not None:
                    out[field] = v
    return out


def normalize(extracts: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (per-pupil spending records in the TGES shape, budget records)."""
    spend: dict[tuple, dict] = {}
    budget: dict[tuple, dict] = {}
    tax_rates: dict[tuple, list] = {}  # set with the proposed budget; kept even after actuals replace it

    def keep(store, key, rec, pri):
        cur = store.get(key)
        if cur is None or pri > cur[0]:
            store[key] = (pri, rec)

    for ex in extracts:
        e = ex["edition"]  # 2027 = 2026-27 budget
        t = ex["tables"]
        # ---- per-pupil costs
        cols = {"year_1": (e - 4, "actual", "audited"), "year_2": (e - 3, "actual", "audited"),
                "year_4": (e - 2, "budgeted", "revised budget"), "year_5": (e - 1, "budgeted", "proposed budget")}
        by_d = defaultdict(list)
        for row in t.get("pupcst", []):
            by_d[row["__d"]].append(row)
        for d, rows in by_d.items():
            for col, (start, basis, stage) in cols.items():
                vals = {}
                for row in rows:
                    m = PER_PUPIL.get(_norm(row.get("line_desc")))
                    v = _num(row.get(col))
                    if m and v is not None:
                        vals[m] = v
                total = vals.get("budgetaryCost")
                for m, v in vals.items():
                    rec = {"year": _year(start), "entity": d, "metric": m, "basis": basis, "stage": stage,
                           "edition": e, "source": "ufb", "value": v}
                    if m in PCT_OF_BUDGETARY and total:
                        rec["pct"] = round(v / total * 100, 1)
                    parent = vals.get(SHARE_PARENT.get(m))
                    if parent:
                        rec["share"] = round(v / parent * 100, 1)
                    keep(spend, (rec["year"], d, m), rec, (basis == "actual", e))
        # ---- budget lines
        cols = {"amount_1": (e - 3, "actual", "audited"), "amount_2": (e - 2, "budgeted", "revised budget"),
                "amount_3": (e - 1, "budgeted", "proposed budget")}
        districts = {r["__d"] for k in ("approp", "rev", "enroll") for r in t.get(k, [])}
        for d in districts:
            ap = [r for r in t.get("approp", []) if r["__d"] == d]
            rv = [r for r in t.get("rev", []) if r["__d"] == d]
            en = [r for r in t.get("enroll", []) if r["__d"] == d]
            for col, (start, basis, stage) in cols.items():
                rec = {"year": _year(start), "districtCode": d, "basis": basis, "stage": stage, "edition": e}
                rec |= _pick(ap, APPROP_LINES, col) | _pick(rv, REV_LINES, col)
                full = [_num(r.get(col)) for r in en if _norm(r.get("enroll_cat")) in ("onrollfull", "onrollspedfull")]
                if any(v is not None for v in full):
                    rec["enrollmentOnRoll"] = sum(v for v in full if v is not None)
                if rec.get("totalOperatingBudget") and rec.get("enrollmentOnRoll"):
                    rec["operatingBudgetPerPupil"] = round(rec["totalOperatingBudget"] / rec["enrollmentOnRoll"])
                if col == "amount_3":
                    rates = []
                    for r in t.get("esttax", []):
                        if r["__d"] != d:
                            continue
                        rates.append({"municipality": (r.get("reg_name") or "").title() or None,
                                      "levy": _num(r.get("gen_fun_sch_levy")),
                                      "rate": _num(r.get("strwo_gfst_rate")),
                                      "equalizedRate": _num(r.get("estrwo_gfst_rate"))})
                    if rates:
                        rec["taxRates"] = rates
                        tax_rates[(rec["year"], d)] = rates
                if len(rec) > 5:
                    keep(budget, (rec["year"], d), rec, (basis == "actual", e))

    budget_out = []
    for (year, d), (_, rec) in sorted(budget.items()):
        if (year, d) in tax_rates:
            rec["taxRates"] = tax_rates[(year, d)]
        budget_out.append(rec)
    return [r for _, r in sorted(spend.values(), key=lambda pr: (pr[1]["year"], pr[1]["entity"], pr[1]["metric"]))], budget_out


def merge_spending(tges_records: list[dict], ufb_records: list[dict]) -> list[dict]:
    """Combine the Taxpayers' Guide and UFB per-pupil series.

    Audited actuals beat budgets. When both sources have an actual, the Taxpayers'
    Guide is kept (it carries NJDOE's ranks and matches the state averages). When both
    are budgets, the more recently published one wins (UFB edition E is published
    a year before TGES edition E)."""
    def pri(r):
        if r.get("source") == "ufb":
            published = r["edition"] - 1
            return (r["basis"] == "actual", 0 if r["basis"] == "actual" else published)
        return (r["basis"] == "actual", 1 if r["basis"] == "actual" else r["edition"])
    best = {}
    for r in tges_records + ufb_records:
        k = (r["year"], r["entity"], r["metric"])
        if k not in best or pri(r) > pri(best[k]):
            best[k] = r
    return sorted(best.values(), key=lambda r: (r["year"], r["entity"], r["metric"]))

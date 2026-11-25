# BRRSD Transparency Dashboard

An independent civic dashboard for the **Bridgewater-Raritan Regional School District** (NJDOE 35-0555). It is built entirely from New Jersey Department of Education School Performance Reports and updates itself with no manual downloads:

```
GitHub Actions (monthly cron)
  └─ scripts/update_njdoe_data.py
       ├─ crawls nj.gov/education/spr/download/ (+ archive) → finds every year's .xlsx
       ├─ HEAD-checks each file; downloads only new or changed years
       ├─ streams the workbooks, keeps only our 5 districts' rows → data/extracts/*.json.gz
       ├─ from each district file, ranks every NJ district → data/extracts/statewide/*.json.gz
       ├─ crawls nj.gov/education/guide/ (Taxpayers' Guide to Education Spending, 2017+ editions)
       │    → keeps our districts + state/group averages → data/extracts/tges/*.json.gz
       ├─ crawls nj.gov/education/budget/ufb/ (User-Friendly Budget summaries, 2020-21+)
       │    → keeps our districts' rows → data/extracts/ufb/*.json.gz
       └─ normalizes everything → public/data/brrsdData.json + public/data/exports/*.csv
  └─ commits the changes as github-actions[bot]
Cloudflare Pages sees the push → rebuilds and redeploys the static React site
```

## What's tracked

| | |
|---|---|
| Districts | Bridgewater-Raritan (target); peers Bernards (35-0350), Montgomery (35-3320), Hillsborough (35-2170), Watchung Hills (35-5550) |
| Years | 2014-15 → latest NJDOE release (2019-20 and 2020-21 have no state tests) |
| Eras | `PARCC` (2014-15 to 2018-19), `COVID_WAIVER` (2019-20, 2020-21), `NJSLA` (2021-22 onward) |
| Proficiency | ELA, Math and Science, district and school; by grade/test, including Algebra I and a weighted Grades 5-8 Math composite |
| Growth | Median Student Growth Percentiles (Grades 4-8 ELA/Math, districtwide, subgroups) against the 50.0 state median |
| Attendance | Chronic absenteeism, district and school |
| Staffing | Teachers, administrators, enrollment, student-to-staff ratios |
| High school | AP/IB course participation, students scoring 3+ on an AP exam, dual enrollment, SAT averages and benchmark rates, and NJ 4- and 5-year adjusted cohort graduation rates, with state comparisons (`highschool.csv`) |
| Top 20 benchmarks | Every NJ district ranked each year (2016-17 →) on ELA, Math, Science, Algebra I, Grades 5-8 Math, mSGP, chronic absenteeism, AP/IB participation, AP scores of 3+, dual enrollment, SAT average and 4- and 5-year graduation rates, by subgroup; Top 20 / Top 10 cutoffs, median, best, and the 20 leading districts |
| Budgets | User-Friendly Budget summaries: operating budget, tax levy, state aid, benefits, 30 budget lines, enrollment and estimated tax rates, 2018-19 → the newest proposed budget |
| Spending | NJDOE Taxpayers' Guide (TGES) indicators 1-19 + total spending, 2014-15 → latest budget year, with state and comparison-group averages and NJDOE ranks |
| Subgroups | All Students, Economically Disadvantaged, Students with Disabilities |

## Dashboard views

1. **District Overview**: proficiency trend across PARCC, the waiver gap and NJSLA, with change vs. the 2018-19 baseline and the Top 20 cutoff (or state average) as the reference line.
2. **Top 20 Goal**: BRRSD's statewide rank and distance to the Top 20 cutoff on every measure (state tests, high school AP/SAT/graduation, attendance), neighbors vs. the cutoff, rank over time, and the districts currently in the Top 20.
3. **Neighbor Recovery**: change in proficiency per year since 2021-22, and share of the pandemic drop recovered, vs. peers.
4. **Growth (mSGP)**: mSGP by grade against the state median of 50, with grades below 50 flagged.
5. **Schools & Algebra I**: school-by-school heat table (red = below the goal, green = above; goal = Top 20 cutoff or state average) with subgroup filter, Algebra I by subgroup, and Grades 5-8 Math vs. peers.
6. **Budget & Staffing**: teacher and administrator headcounts and ratios, plus budget figures from `public/data/fiscal_overrides.json`.
7. **Spending vs. Peers**: classroom instruction, support services, administration, operations and extracurricular spending as a % of budgetary cost (or $ per pupil), compared with the peers, the median of the Top 20 achieving districts, the state average and the K-12 3,501+ group average; a variance table (BRRSD minus the peer average, Top 20 median and state average, in $ and %), with administration cost per pupil highlighted; trends since 2014-15; staff ratios and median salaries; a table of every TGES indicator with BRRSD's NJDOE rank.

Every chart has **CSV / JSON download** buttons and a table view. Full CSVs are in `public/data/exports/`.

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/update_njdoe_data.py            # incremental update
python scripts/update_njdoe_data.py --backfill # first-time / full rebuild (~1.1 GB download, ~6 min)
python scripts/update_njdoe_data.py --rebuild  # offline: re-normalize from data/extracts only
npm install
npm run dev
```

Other flags: `--years 2024-25 --force`, `--dry-run` (discover only), `--download-dir DIR` (keep the raw .xlsx files), `-v`.

## Publishing (GitHub + Cloudflare Pages)

The site is a static Vite build hosted free on Cloudflare Pages, connected to this GitHub repository:

| Setting | Value |
|---|---|
| Framework preset | Vite (or none) |
| Build command | `npm run build` |
| Build output directory | `dist` |
| Node version | from `.node-version` (22) |

Every push to `main`, including the monthly data commit from GitHub Actions, triggers a new deploy. The custom domain is attached under the Pages project's Custom domains settings.

## Budget figures

Budget figures now load automatically from NJDOE's [User-Friendly Budget summaries](https://www.nj.gov/education/budget/ufb/) (`scripts/njdoe_ufb.py`). Each edition gives one year of audited actuals, the current year's revised budget and next year's proposed budget. Each year is shown at the most final stage available (proposed → revised → audited), and the stage is labeled in the dashboard. The budgets' per-pupil cost tables use the same indicators as the Taxpayers' Guide, so they extend Spending vs. Peers to the newest budget year. Those newest years have no state or group averages until NJDOE's next Taxpayers' Guide comes out. Full rows are in `public/data/exports/budget.csv`.

`public/data/fiscal_overrides.json` is still read: any non-null value there overrides the NJDOE figure for that year (for example, to correct a figure from the district's own documents).

## Top 20 benchmarks

For each year from 2016-17, the pipeline runs the same extractors over every district-level row in NJDOE's district file and ranks the districts. The **Top 20 cutoff** is the score of the 20th-best district: matching it would put BRRSD in the Top 20. The default comparison is NJ **K-12 districts** (at least 100 students in K-5 and 25 in grade 12; charters and county vocational schools excluded), about 225 districts a year, because K-8 elementary districts and small charters aren't comparable to a K-12 district. "All NJ districts & charters" is available as an alternative. Suppressed values are excluded, never estimated. High-school measures rank every district that runs a high school (K-12 and 9-12 regionals, about 270), so Watchung Hills is included there. 5-year graduation rates arrive a year late, so they're benchmarked against the year of the cohort. Full results are in `public/data/exports/benchmarks.csv`.

**Top 20 districts for spending.** The 20 K-12 districts with the highest average of ELA and Math proficiency each year form the "Top 20" used on the Spending tab. The Taxpayers' Guide is read for every NJ district, and each indicator's median across those 20 is published as entity `TOP20_MEDIAN`. Each spending year uses the latest Top 20 list at or before it: 2019-20 and 2020-21 had no tests and use 2018-19, and years before 2016-17 use 2016-17.

## Spending data (Taxpayers' Guide to Education Spending)

`scripts/njdoe_tges.py` reads every TGES edition from 2017 on: the 2017-2023 ZIPs of coded CSVs and the 2024+ Excel files. Each edition covers two actual years and one budget year. The dashboard keeps the newest actual figure for each year, and a year is labeled *budgeted* only until NJDOE publishes its actuals. Spending is compared on NJDOE's **budgetary cost per pupil** basis, which allocates benefits to the category where staff work and excludes transportation, out-of-district tuition, debt, capital outlay and state-paid pensions. Total spending per pupil (every fund) is shown separately and isn't comparable to it. Bridgewater-Raritan, Bernards, Montgomery and Hillsborough are in NJDOE group G (K-12, 3,501+ students); Watchung Hills is in group H (7-12/9-12). Use `--skip-spending` to skip this step.

## Notes on method

- **Proficiency** is the percent of valid test takers who Met or Exceeded Expectations (Level 4+ for ELA/Math, Level 3+ for Science).
- **2014-15 and 2015-16** were published only at school level. District figures for those years are weighted averages of school rows, by valid scores, and are labeled `derived` in the data.
- **Suppressed values** (`*`, `N`, `<10`) are left blank, never estimated.
- **NJDOE changes its column names** most years and switched to a wide, multi-year layout in 2024-25. The ETL matches sheets and columns by pattern, with a fuzzy fallback, so small header changes don't break it. If a new layout drops a metric, the run logs which sheets it kept but couldn't map (`-v`).
- `lastVerifiedAgainstNJDOE` is updated every run, so the monthly job always commits. That is intended: it keeps the "Last verified" badge honest.

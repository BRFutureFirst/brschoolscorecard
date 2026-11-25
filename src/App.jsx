import { useEffect, useMemo, useState } from 'react'
import {
  Bar, BarChart, CartesianGrid, Cell, LabelList, Legend, Line, LineChart, ReferenceArea, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'

/* ------------------------------------------------------------------------- */
/* Constants                                                                  */
/* ------------------------------------------------------------------------- */
const TARGET = '35-0555'
const BASELINE = '2018-19'
const RECOVERY_START = '2021-22'
const WAIVER_YEARS = ['2019-20', '2020-21']
const ALL = 'All Students'
const GROUPS = ['All Students', 'Economically Disadvantaged', 'Students with Disabilities']
// Color follows the entity, in fixed categorical order.
const DISTRICT_COLOR = {
  '35-0555': 'var(--series-1)',
  '35-0350': 'var(--series-2)',
  '35-3320': 'var(--series-3)',
  '35-2170': 'var(--series-4)',
  '35-5550': 'var(--series-5)',
}
// Display order: target first, then peers.
const DISTRICT_ORDER = ['35-0555', '35-0350', '35-3320', '35-2170', '35-5550']
const orderedDistricts = (meta) => DISTRICT_ORDER.filter((d) => meta.districts[d])
const SUBJECT_COLOR = { ELA: 'var(--series-1)', Math: 'var(--series-2)', Science: 'var(--series-3)' }
const GROUP_COLOR = { [GROUPS[0]]: 'var(--series-1)', [GROUPS[1]]: 'var(--series-2)', [GROUPS[2]]: 'var(--series-3)' }
const TABS = [
  { id: 'overview', label: 'District Overview' },
  { id: 'achievement', label: 'Top 20 Goal' },
  { id: 'recovery', label: 'Neighbor Recovery' },
  { id: 'growth', label: 'Growth (mSGP)' },
  { id: 'schools', label: 'Schools & Algebra I' },
  { id: 'budget', label: 'Budget & Staffing' },
  { id: 'spending', label: 'Spending vs. Peers' },
]

/* ------------------------------------------------------------------------- */
/* Helpers                                                                    */
/* ------------------------------------------------------------------------- */
const fmt = (v, d = 1) => (v == null || Number.isNaN(v) ? '–' : Number(v).toFixed(d))
const fmtPct = (v) => (v == null ? '–' : `${fmt(v)}%`)
const signed = (v, unit = ' pts') => (v == null ? '–' : `${v > 0 ? '+' : v < 0 ? '−' : '±'}${fmt(Math.abs(v))}${unit}`)
const money = (v) => (v == null ? '–' : v >= 1e6 ? `$${(v / 1e6).toFixed(1)}M` : `$${Math.round(v).toLocaleString()}`)

function match(r, f) {
  for (const [k, v] of Object.entries(f)) {
    if (v === null) { if (r[k] != null) return false }
    else if (r[k] !== v) return false
  }
  return true
}
const where = (rows, f) => (rows || []).filter((r) => match(r, f))
const first = (rows, f) => (rows || []).find((r) => match(r, f))

function toCSV(rows) {
  if (!rows?.length) return ''
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))]
  const esc = (v) => {
    if (v == null) return ''
    const s = String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  return [cols.join(','), ...rows.map((r) => cols.map((c) => esc(r[c])).join(','))].join('\n')
}
function save(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }))
  const a = Object.assign(document.createElement('a'), { href: url, download: name })
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/* ------------------------------------------------------------------------- */
/* Building blocks                                                            */
/* ------------------------------------------------------------------------- */
function DownloadButtons({ name, rows }) {
  const disabled = !rows?.length
  return (
    <div className="flex gap-2">
      <button className="btn" disabled={disabled} onClick={() => save(`${name}.csv`, toCSV(rows), 'text/csv')}
        aria-label={`Download ${name} as CSV`}>⬇ CSV</button>
      <button className="btn" disabled={disabled}
        onClick={() => save(`${name}.json`, JSON.stringify(rows, null, 2), 'application/json')}
        aria-label={`Download ${name} as JSON`}>⬇ JSON</button>
    </div>
  )
}

function Card({ title, subtitle, name, rows, children, table, footnote }) {
  const [showTable, setShowTable] = useState(false)
  return (
    <section className="card p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-3">
        <div className="min-w-0">
          <h3 className="text-base font-semibold">{title}</h3>
          {subtitle && <p className="text-sm text-secondary mt-0.5">{subtitle}</p>}
        </div>
        <div className="flex gap-2 flex-wrap">
          {table && <button className="btn" onClick={() => setShowTable((s) => !s)} aria-pressed={showTable}>
            {showTable ? 'Show chart' : 'Show table'}</button>}
          {name && <DownloadButtons name={name} rows={rows} />}
        </div>
      </div>
      {showTable && table ? <div className="overflow-auto max-h-[420px]">{table}</div> : children}
      {footnote && <p className="text-xs text-muted mt-3 leading-relaxed">{footnote}</p>}
    </section>
  )
}

function DataTable({ rows, columns }) {
  return (
    <table className="data-table tabular">
      <thead><tr>{columns.map((c) => <th key={c.key} scope="col">{c.label}</th>)}</tr></thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>{columns.map((c) => <td key={c.key}>{c.render ? c.render(r[c.key], r) : r[c.key] ?? '–'}</td>)}</tr>
        ))}
      </tbody>
    </table>
  )
}

function Stat({ label, value, delta, note, deltaLabel, lowerIsBetter = false, neutral = false, unit = ' pts' }) {
  // neutral: the direction is informative but not good/bad (e.g., spending mix), so no status color.
  const better = delta == null || neutral ? null : lowerIsBetter ? delta <= 0 : delta >= 0
  const tone = better == null ? 'var(--text-muted)' : better ? 'var(--good)' : 'var(--critical)'
  return (
    <div className="card p-4 kpi-accent">
      <div className="text-xs font-medium uppercase tracking-wide text-muted">{label}</div>
      <div className="text-3xl font-semibold mt-1 tabular">{value}</div>
      {delta !== undefined && (
        <div className="text-sm mt-1 tabular flex items-center gap-1.5">
          <span aria-hidden="true" style={{ color: tone }}>{delta == null ? '•' : delta >= 0 ? '▲' : '▼'}</span>
          <span className="sr-only">{better == null ? '' : better ? 'improved' : 'worsened'}</span>
          <span className="text-secondary">{signed(delta, unit)} {deltaLabel}</span>
        </div>
      )}
      {note && <div className="text-xs text-muted mt-1">{note}</div>}
    </div>
  )
}

function Control({ label, value, onChange, options }) {
  return (
    <label className="flex items-center gap-2 text-sm text-secondary">
      <span>{label}</span>
      <select className="select" value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => (typeof o === 'string'
          ? <option key={o} value={o}>{o}</option>
          : <option key={o.value} value={o.value}>{o.label}</option>))}
      </select>
    </label>
  )
}

const axisProps = { stroke: 'var(--grid)', tickLine: false }
const pctAxis = { ...axisProps, domain: [0, 100], tickFormatter: (v) => `${v}%`, width: 44 }
const gridProps = { stroke: 'var(--grid)', strokeDasharray: '0', vertical: false }
const tooltipFmt = (v) => (typeof v === 'number' ? `${fmt(v)}%` : v)

// Recharts 2 only renders reference elements that are direct children, so this is a
// plain function returning the element rather than a component.
function waiverBand(years) {
  if (!years.includes(WAIVER_YEARS[0]) || !years.includes(WAIVER_YEARS[1])) return null
  return (
    <ReferenceArea x1={WAIVER_YEARS[0]} x2={WAIVER_YEARS[1]} fill="var(--band)" stroke="none" ifOverflow="extendDomain"
      label={{ value: 'Testing waived (COVID)', position: 'insideTop', fill: 'var(--text-muted)', fontSize: 11 }} />
  )
}

/* ------------------------------------------------------------------------- */
/* View 1: Overview & pre-pandemic baseline                                   */
/* ------------------------------------------------------------------------- */
const SUBJECT_METRIC = { ELA: 'ela', Math: 'math', Science: 'science' }

// Why the site exists and how to read it. Shown at the top of the Overview tab.
const ABOUT_TABS = [
  ['achievement', 'Top 20 Goal', 'BRRSD\'s statewide rank on every measure, from state tests to AP, SAT and graduation, and how far it is from the Top 20.'],
  ['recovery', 'Recovery & Growth', 'How quickly scores have recovered since the pandemic, and how much students grow each year.'],
  ['schools', 'Schools & Algebra I', 'Every BRRSD school, color-coded against the Top 20 cutoff or the state average.'],
  ['spending', 'Budget & Spending', 'Where each dollar goes, compared with neighbors, the state and the Top 20 districts.'],
]
function About({ topN }) {
  return (
    <section className="card p-4 sm:p-5 text-sm leading-relaxed" aria-labelledby="about-heading">
      <h2 id="about-heading" className="text-base font-semibold">Why this site exists</h2>
      <p className="mt-1 text-secondary max-w-3xl">
        Every Bridgewater-Raritan student deserves the best possible education, at every grade and in every school. This site
        gives residents and parents a clear, easy look at how the district is doing: how it ranks against its neighbors
        (Bernards, Montgomery, Hillsborough and Watchung Hills), against the state, and against New Jersey's Top {topN} school
        districts, on both state test results and budgets. The goal is transparency, so the community can focus on what
        matters most: student outcomes.
      </p>
      <div className="mt-3 grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
        {ABOUT_TABS.map(([id, title, text]) => (
          <a key={id} href={`#${id}`} className="about-link rounded-lg p-3 block" style={{ background: 'var(--surface-2)' }}>
            <div className="font-semibold">{title} <span aria-hidden="true" className="text-muted">→</span></div>
            <div className="text-xs text-secondary mt-0.5">{text}</div>
          </a>
        ))}
      </div>
      <p className="mt-3 text-xs text-muted max-w-3xl">
        Every number comes from New Jersey Department of Education public reports: School Performance Reports, the Taxpayers'
        Guide to Education Spending and User-Friendly Budgets. Nothing is estimated, and suppressed values are left blank. Each
        chart can be downloaded as CSV. This is an independent project, not affiliated with the school district.
      </p>
    </section>
  )
}

function Overview({ data }) {
  const [group, setGroup] = useState(ALL)
  const [ref, setRef] = useState('top20')
  const bench = useBench(data)
  const topN = data.meta.benchmarks?.topN ?? 20
  const refLabel = ref === 'top20' ? `Top ${topN} cutoff` : 'state'
  const years = data.meta.years
  const rows = useMemo(() => years.map((year) => {
    const row = { year, era: null }
    for (const s of ['ELA', 'Math', 'Science']) {
      const r = first(data.proficiency, { year, districtCode: TARGET, schoolCode: null, subject: s, test: 'All', group })
      row[s] = r?.rate ?? null
      row[`${s} (state)`] = r?.stateRate ?? null
      row[`${s} (Top ${topN})`] = bench(year, SUBJECT_METRIC[s], group)?.top20 ?? null
      row[`${s} rank`] = bench(year, SUBJECT_METRIC[s], group)?.ranks?.[TARGET] ?? null
      if (r) row.era = r.era
    }
    return row
  }), [data, group, years, bench, topN])
  const refKey = (s) => (ref === 'top20' ? `${s} (Top ${topN})` : `${s} (state)`)
  const latestBench = (m) => bench(latest?.year, m, group)

  const latest = [...rows].reverse().find((r) => r.ELA != null || r.Math != null)
  const base = rows.find((r) => r.year === BASELINE)
  const absLatest = [...where(data.absenteeism, { districtCode: TARGET, schoolCode: null, group })]
    .sort((a, b) => a.year.localeCompare(b.year)).pop()
  const absBase = first(data.absenteeism, { districtCode: TARGET, schoolCode: null, group, year: BASELINE })

  return (
    <div className="space-y-5">
      <About topN={topN} />
      <div className="flex flex-wrap gap-4 items-center">
        <Control label="Student group" value={group} onChange={setGroup} options={GROUPS} />
        <Control label="Compare with" value={ref} onChange={setRef}
          options={[{ value: 'top20', label: `Top ${topN} cutoff (K-12 districts)` }, { value: 'state', label: 'State average' }, { value: 'none', label: 'Nothing' }]} />
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {['ELA', 'Math', 'Science'].map((s) => (
          <Stat key={s} label={`${s} proficiency · ${latest?.year ?? ''}`} value={fmtPct(latest?.[s])}
            delta={latest?.[s] != null && base?.[s] != null ? latest[s] - base[s] : null}
            deltaLabel={`vs ${BASELINE}`}
            note={latestBench(SUBJECT_METRIC[s])
              ? `Top ${topN} cutoff ${fmtPct(latestBench(SUBJECT_METRIC[s]).top20)} · rank ${latestBench(SUBJECT_METRIC[s]).ranks?.[TARGET] ?? '–'} of ${latestBench(SUBJECT_METRIC[s]).n}`
              : latest?.[`${s} (state)`] != null ? `State: ${fmtPct(latest[`${s} (state)`])}` : undefined} />
        ))}
        <Stat label={`Chronic absenteeism · ${absLatest?.year ?? ''}`} value={fmtPct(absLatest?.rate)}
          delta={absLatest && absBase ? absLatest.rate - absBase.rate : null} lowerIsBetter
          deltaLabel={`vs ${BASELINE} (lower is better)`}
          note={bench(absLatest?.year, 'absenteeism', group)
            ? `Top ${topN} cutoff ${fmtPct(bench(absLatest.year, 'absenteeism', group).top20)} · rank ${bench(absLatest.year, 'absenteeism', group).ranks?.[TARGET] ?? '–'} of ${bench(absLatest.year, 'absenteeism', group).n}`
            : absLatest?.stateRate != null ? `State: ${fmtPct(absLatest.stateRate)}` : undefined} />
      </div>
      <Card title="Proficiency trend: PARCC → waiver gap → NJSLA"
        subtitle={`Bridgewater-Raritan, ${group}: % meeting or exceeding expectations`}
        name={`brrsd_overview_${group.replace(/\s+/g, '_').toLowerCase()}`} rows={rows}
        table={<DataTable rows={rows} columns={[
          { key: 'year', label: 'Year' }, { key: 'era', label: 'Era' },
          ...['ELA', 'Math', 'Science'].flatMap((s) => [
            { key: s, label: s, render: fmtPct }, { key: `${s} (Top ${topN})`, label: `${s} Top ${topN} cutoff`, render: fmtPct },
            { key: `${s} rank`, label: `${s} rank`, render: (v) => v ?? '–' }, { key: `${s} (state)`, label: `${s} state`, render: fmtPct }]),
        ]} />}
        footnote={`Dashed lines show the ${refLabel} for each subject${ref === 'top20' ? ` (the score of the ${topN}th-best NJ K-12 district; computed from 2016-17 on)` : ''}. The dashed vertical line marks the 2018-19 pre-pandemic baseline. NJ gave no statewide ELA/Math/Science tests in spring 2020 or spring 2021, so those years are blank. PARCC (2015-2018) and NJSLA (2019-) share a blueprint and scale, but NJDOE cautions against over-reading small year-to-year changes across the transition. Science (NJSLA-S) begins in 2018-19.`}>
        <ResponsiveContainer width="100%" height={340}>
          <LineChart data={rows} margin={{ top: 20, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid {...gridProps} />
            <XAxis dataKey="year" {...axisProps} />
            <YAxis {...pctAxis} />
            <Tooltip formatter={tooltipFmt} />
            <Legend />
            {waiverBand(years)}
            <ReferenceLine x={BASELINE} stroke="var(--text-muted)" strokeDasharray="4 4"
              label={{ value: 'Baseline', position: 'insideTopLeft', fill: 'var(--text-muted)', fontSize: 11 }} />
            {['ELA', 'Math', 'Science'].map((s) => (
              <Line key={s} type="linear" dataKey={s} stroke={SUBJECT_COLOR[s]} strokeWidth={2} dot={{ r: 4 }}
                activeDot={{ r: 6 }} connectNulls={false} isAnimationActive={false} />
            ))}
            {ref !== 'none' && ['ELA', 'Math', 'Science'].map((s) => (
              <Line key={`${s}-ref`} dataKey={refKey(s)} stroke={SUBJECT_COLOR[s]} strokeWidth={1.5}
                strokeDasharray="5 4" dot={false} strokeOpacity={0.7} connectNulls={false} isAnimationActive={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </Card>
    </div>
  )
}

/* ------------------------------------------------------------------------- */
/* Statewide benchmarks (Top 20 cutoffs, ranks)                               */
/* ------------------------------------------------------------------------- */
// How each benchmark metric is read from the district datasets.
const METRIC_SPEC = {
  ela: { ds: 'proficiency', f: { subject: 'ELA', test: 'All' }, field: 'rate' },
  math: { ds: 'proficiency', f: { subject: 'Math', test: 'All' }, field: 'rate' },
  science: { ds: 'proficiency', f: { subject: 'Science', test: 'All' }, field: 'rate' },
  algebra1: { ds: 'proficiency', f: { subject: 'Math', test: 'Algebra I' }, field: 'rate' },
  math58: { ds: 'proficiency', f: { subject: 'Math', test: 'Grades 5-8' }, field: 'rate' },
  msgpEla: { ds: 'growth', f: { subject: 'ELA', grade: 'All' }, field: 'msgp' },
  msgpMath: { ds: 'growth', f: { subject: 'Math', grade: 'All' }, field: 'msgp' },
  absenteeism: { ds: 'absenteeism', f: {}, field: 'rate' },
  grad4: { ds: 'highschool', f: { metric: 'grad4' }, field: 'value' },
  grad5: { ds: 'highschool', f: { metric: 'grad5' }, field: 'value' },
  apCourse: { ds: 'highschool', f: { metric: 'apCourse' }, field: 'value' },
  apScore3: { ds: 'highschool', f: { metric: 'apScore3' }, field: 'value' },
  dualEnroll: { ds: 'highschool', f: { metric: 'dualEnroll' }, field: 'value' },
  satTotal: { ds: 'highschool', f: { metric: 'satTotal' }, field: 'value' },
}
const METRIC_SECTIONS = [
  { title: 'State tests (grades 3-11)', metrics: ['ela', 'math', 'science', 'algebra1', 'math58', 'msgpEla', 'msgpMath'] },
  { title: 'High school: AP, SAT and graduation', metrics: ['apCourse', 'apScore3', 'satTotal', 'grad4', 'grad5', 'dualEnroll'] },
  { title: 'Attendance', metrics: ['absenteeism'] },
]
const METRIC_ORDER = METRIC_SECTIONS.flatMap((s) => s.metrics)
const UNIVERSE_SHORT = { k12: 'NJ K-12 districts', all: 'all NJ districts' }
const universeShort = (meta, metric, universe) => (universe === 'k12' && meta?.metrics?.[metric]?.highSchool ? 'NJ districts with a high school' : UNIVERSE_SHORT[universe])

function useBench(data) {
  return useMemo(() => {
    const m = new Map()
    for (const b of data.benchmarks ?? []) m.set(`${b.year}|${b.metric}|${b.group}|${b.universe}`, b)
    return (year, metric, group, universe = 'k12') => m.get(`${year}|${metric}|${group}|${universe}`)
  }, [data])
}

// get(metric, year, districtCode, group) -> value from the published district datasets.
function useMetricValue(data) {
  return useMemo(() => {
    const m = new Map()
    for (const [metric, spec] of Object.entries(METRIC_SPEC)) {
      for (const r of data[spec.ds] ?? []) {
        if (r.schoolCode || !match(r, spec.f)) continue
        m.set(`${metric}|${r.year}|${r.districtCode}|${r.group}`, r[spec.field])
      }
    }
    return (metric, year, d, group) => m.get(`${metric}|${year}|${d}|${group}`) ?? null
  }, [data])
}

const benchFmt = (meta, metric) => (v) => (v == null ? '–'
  : meta?.metrics?.[metric]?.unit === 'score' ? Math.round(v).toLocaleString()
    : meta?.metrics?.[metric]?.unit === 'sgp' ? fmt(v) : fmtPct(v))
// Positive = short of the goal, in the metric's own units (points).
function shortfall(meta, metric, value, goal) {
  if (value == null || goal == null) return null
  return meta.metrics[metric].higherIsBetter ? goal - value : value - goal
}

function GoalTile({ meta, metric, value, bench, year }) {
  const info = meta.metrics[metric]
  const f = benchFmt(meta, metric)
  const rank = bench?.ranks?.[TARGET]
  const short = shortfall(meta, metric, value, bench?.top20)
  const inTop = rank != null && rank <= meta.topN
  // Meter runs from the state median (left) to the best district (right); lower-is-better metrics are flipped.
  const pos = (x) => {
    if (x == null || !bench) return null
    const t = info.higherIsBetter ? (x - bench.median) / (bench.best - bench.median) : (bench.median - x) / (bench.median - bench.best)
    return Math.max(0, Math.min(1, t)) * 100
  }
  return (
    <div className="card p-4 kpi-accent flex flex-col gap-2">
      <div className="text-xs font-medium uppercase tracking-wide text-muted">{info.label}</div>
      <div className="flex items-baseline justify-between gap-2">
        <div className="text-3xl font-semibold tabular">{f(value)}</div>
        {rank != null && <div className="text-sm text-secondary tabular text-right">#{rank}<span className="text-muted"> of {bench.n}</span></div>}
      </div>
      {bench && value != null ? (
        <>
          <div className="relative h-2.5 rounded-full mt-1" style={{ background: 'var(--surface-2)' }} aria-hidden="true">
            <div className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${pos(value)}%`, background: 'var(--series-1)' }} />
            <div className="absolute -top-1 -bottom-1 w-0.5" style={{ left: `${pos(bench.top20)}%`, background: 'var(--text-primary)' }} />
          </div>
          <div className="flex justify-between text-[11px] text-muted tabular">
            <span>Median {f(bench.median)}</span><span>Top {meta.topN} {f(bench.top20)}</span><span>Best {f(bench.best)}</span>
          </div>
          <div>
            {inTop
              ? <span className="chip" style={{ color: 'var(--good)', borderColor: 'var(--good)' }}><span aria-hidden="true">✓</span> In the Top {meta.topN}</span>
              : <span className="chip"><span aria-hidden="true" style={{ color: 'var(--warning)' }}>▲</span>
                {info.unit === 'score' ? Math.round(Math.abs(short)) : fmt(Math.abs(short))} pts {info.higherIsBetter ? 'below' : 'above'} Top {meta.topN} cutoff</span>}
          </div>
        </>
      ) : <div className="text-xs text-muted">{value == null ? `No ${year} result` : 'No statewide benchmark for this year'}</div>}
    </div>
  )
}

/* ------------------------------------------------------------------------- */
/* View: Achievement vs. peers and the Top 20                                 */
/* ------------------------------------------------------------------------- */
function Achievement({ data }) {
  const meta = data.meta.benchmarks
  const bench = useBench(data)
  const get = useMetricValue(data)
  const years = meta?.years ?? []
  const [year, setYear] = useState(years[years.length - 1])
  const [group, setGroup] = useState(ALL)
  const [universe, setUniverse] = useState('k12')
  const [trendMetric, setTrendMetric] = useState('math')
  if (!meta || !years.length) return <div className="card p-4">Statewide benchmarks have not been built yet.</div>
  const topN = meta.topN
  // Watchung Hills (9-12) is only compared on high-school measures.
  const allDistricts = orderedDistricts(data.meta)
  const districtsFor = (m) => (meta.metrics[m]?.highSchool ? allDistricts : allDistricts.filter((d) => data.meta.districts[d].grades !== '09-12'))
  const name = (d) => data.meta.districts[d].short
  const b = (m, y = year) => bench(y, m, group, universe)
  const metrics = METRIC_ORDER.filter((m) => years.some((y) => b(m, y)))
  const tm = metrics.includes(trendMetric) ? trendMetric : metrics[0]
  const tf = benchFmt(meta, tm)
  const districts = districtsFor(tm)
  const peerCols = allDistricts.filter((d) => d !== TARGET)

  const scoreRows = metrics.map((m) => {
    const bb = b(m), v = get(m, year, TARGET, group)
    const short = shortfall(meta, m, v, bb?.top20)
    return {
      metric: meta.metrics[m].label, year, group, comparedWith: universeShort(meta, m, universe),
      brrsd: v, rank: bb?.ranks?.[TARGET] ?? null, districtsRanked: bb?.n ?? null,
      top20Cutoff: bb?.top20 ?? null, top10Cutoff: bb?.top10 ?? null, best: bb?.best ?? null, median: bb?.median ?? null,
      ptsToTop20: short == null ? null : Number(Math.max(0, short).toFixed(1)),
      ...Object.fromEntries(peerCols.map((d) => [`${name(d)} rank`, bb?.ranks?.[d] ?? null])),
    }
  })
  const reached = scoreRows.filter((r) => r.rank != null && r.rank <= topN).length
  const ranked = scoreRows.filter((r) => r.rank != null).length

  const trendYears = data.meta.years.filter((y) => y >= years[0])
  const trend = trendYears.map((y) => {
    const row = { year: y }
    for (const d of districts) row[name(d)] = get(tm, y, d, group)
    row[`Top ${topN} cutoff`] = b(tm, y)?.top20 ?? null
    row['Top 10 cutoff'] = b(tm, y)?.top10 ?? null
    row['Statewide median'] = b(tm, y)?.median ?? null
    return row
  })
  const rankTrend = trendYears.map((y) => {
    const row = { year: y }
    for (const d of districts) row[name(d)] = b(tm, y)?.ranks?.[d] ?? null
    return row
  })
  const maxRank = Math.max(topN + 5, ...rankTrend.flatMap((r) => districts.map((d) => r[name(d)] ?? 0)))

  const leadersBench = bench(year, tm, ALL, universe)
  const leaders = (leadersBench?.leaders ?? []).map((l, i) => ({ rank: i + 1, ...l }))
  const extra = districts.filter((d) => !leaders.some((l) => l.districtCode === d) && leadersBench?.ranks?.[d] != null)
    .map((d) => ({ rank: leadersBench.ranks[d], districtCode: d, name: data.meta.districts[d].name, value: get(tm, year, d, ALL) }))
    .sort((a, z) => a.rank - z.rank)

  return (
    <div className="space-y-5">
      <div className="card p-4 sm:p-5 text-sm text-secondary leading-relaxed">
        <strong className="text-[color:var(--text-primary)]">Aiming for the Top {topN}.</strong> The state average includes
        many districts facing very different circumstances, so beating it is a low bar for Bridgewater-Raritan. Here each
        result is measured against the <strong>Top {topN} cutoff</strong>: the score of the {topN}th-best district in New
        Jersey that year. Matching it would put BRRSD in the Top {topN}. By default the ranking compares {meta.universes.k12.split(' (')[0]} like
        BRRSD, so K-8 elementary districts and small charters aren't included.
      </div>
      <div className="flex flex-wrap gap-4 items-center">
        <Control label="Year" value={year} onChange={setYear} options={[...years].reverse()} />
        <Control label="Student group" value={group} onChange={setGroup} options={GROUPS} />
        <Control label="Rank among" value={universe} onChange={setUniverse}
          options={[{ value: 'k12', label: 'NJ K-12 districts' }, { value: 'all', label: 'All NJ districts & charters' }]} />
        {ranked > 0 && <span className="chip">BRRSD is in the Top {topN} on {reached} of {ranked} measures</span>}
      </div>
      {METRIC_SECTIONS.map((sec) => {
        const ms = sec.metrics.filter((m) => metrics.includes(m))
        if (!ms.length) return null
        return (
          <section key={sec.title} className="space-y-3">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-secondary">{sec.title}</h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
              {ms.map((m) => <GoalTile key={m} meta={meta} metric={m} value={get(m, year, TARGET, group)} bench={b(m)} year={year} />)}
            </div>
          </section>
        )
      })}

      <Card title={`Neighbors vs. the Top ${topN} cutoff · ${year}`} subtitle={`${group}. Bars show each district; lines show what it takes to rank in the Top ${topN} and the statewide median.`}
        name={`achievement_scorecard_${year}`} rows={scoreRows}
        table={<DataTable rows={scoreRows} columns={[
          { key: 'metric', label: 'Measure' }, { key: 'brrsd', label: 'BRRSD', render: (v, r) => benchFmt(meta, metrics[scoreRows.indexOf(r)])(v) },
          { key: 'rank', label: 'Rank', render: (v, r) => (v == null ? '–' : `${v} of ${r.districtsRanked}`) },
          { key: 'top20Cutoff', label: `Top ${topN} cutoff`, render: (v, r) => benchFmt(meta, metrics[scoreRows.indexOf(r)])(v) },
          { key: 'ptsToTop20', label: 'Pts to go', render: (v) => (v == null ? '–' : v === 0 ? '✓' : fmt(v)) },
          ...peerCols.map((d) => ({ key: `${name(d)} rank`, label: `${name(d)} rank`, render: (v) => v ?? '–' })),
        ]} />}
        footnote={`Ranks are among ${meta.universes[universe]} that reported the measure in ${year}. Watchung Hills Regional (grades 9-12) appears only on high-school measures, which rank every district that runs a high school. AP = students in grades 9-12 taking an AP/IB course, and students scoring 3+ on an AP exam (4+ on IB). SAT = average Math + Evidence-Based Reading & Writing. Graduation = NJ adjusted cohort rate. Chronic absenteeism: lower is better.`}>
        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {metrics.map((m) => {
            const bb = b(m), f = benchFmt(meta, m)
            return <PeerBars key={m} title={meta.metrics[m].label} format={f}
              rows={districtsFor(m).map((d) => ({ district: name(d), code: d, v: get(m, year, d, group) }))}
              refs={[{ value: bb?.top20, label: `Top ${topN}`, stroke: 'var(--text-primary)', dash: '5 4' },
                { value: bb?.median, label: 'Median', stroke: 'var(--neutral-series)', dash: '2 3' }]}
              note={bb?.ranks?.[TARGET] ? `BRRSD ranks ${bb.ranks[TARGET]} of ${bb.n}` : null} />
          })}
        </div>
      </Card>

      <div className="grid lg:grid-cols-2 gap-5">
        <Card title={`${meta.metrics[tm].label} vs. the Top ${topN} cutoff`} subtitle={`${group}; statewide benchmarks start ${years[0]}`}
          name={`achievement_trend_${tm}`} rows={trend}
          table={<DataTable rows={trend} columns={[{ key: 'year', label: 'Year' },
            ...[...districts.map(name), `Top ${topN} cutoff`, 'Top 10 cutoff', 'Statewide median'].map((k) => ({ key: k, label: k, render: tf }))]} />}>
          <div className="mb-3"><Control label="Measure" value={tm} onChange={setTrendMetric}
            options={metrics.map((m) => ({ value: m, label: meta.metrics[m].label }))} /></div>
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={trend} margin={{ top: 16, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid {...gridProps} />
              <XAxis dataKey="year" {...axisProps} />
              <YAxis {...axisProps} width={44} domain={['auto', 'auto']} tickFormatter={tf} />
              <Tooltip formatter={tf} />
              <Legend />
              {waiverBand(trendYears)}
              {districts.map((d) => (
                <Line key={d} dataKey={name(d)} stroke={DISTRICT_COLOR[d]} strokeWidth={d === TARGET ? 3 : 2} dot={{ r: d === TARGET ? 5 : 3 }}
                  connectNulls={false} isAnimationActive={false} />
              ))}
              <Line dataKey={`Top ${topN} cutoff`} stroke="var(--text-primary)" strokeDasharray="5 4" strokeWidth={2} dot={false} isAnimationActive={false} />
              <Line dataKey="Top 10 cutoff" stroke="var(--neutral-series)" strokeDasharray="2 3" strokeWidth={1.5} dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </Card>
        <Card title={`Statewide rank: ${meta.metrics[tm].label}`} subtitle={`Rank among ${universeShort(meta, tm, universe)} (1 = best). Shaded band = Top ${topN}.`}
          name={`achievement_rank_${tm}`} rows={rankTrend}
          table={<DataTable rows={rankTrend} columns={[{ key: 'year', label: 'Year' }, ...districts.map((d) => ({ key: name(d), label: name(d), render: (v) => v ?? '–' }))]} />}>
          <div className="mb-3 h-[38px]" />
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={rankTrend} margin={{ top: 16, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid {...gridProps} />
              <XAxis dataKey="year" {...axisProps} />
              <YAxis {...axisProps} width={40} reversed domain={[1, Math.ceil(maxRank / 20) * 20]} allowDecimals={false}
                ticks={[1, ...Array.from({ length: Math.ceil(maxRank / 20) }, (_, i) => (i + 1) * 20)]} />
              <Tooltip formatter={(v) => `#${v}`} />
              <Legend />
              <ReferenceArea y1={1} y2={topN} fill="var(--good)" fillOpacity={0.1} stroke="none"
                label={{ value: `Top ${topN}`, position: 'insideBottomRight', fill: 'var(--text-muted)', fontSize: 11 }} />
              {districts.map((d) => (
                <Line key={d} dataKey={name(d)} stroke={DISTRICT_COLOR[d]} strokeWidth={d === TARGET ? 3 : 2} dot={{ r: d === TARGET ? 5 : 3 }}
                  connectNulls={false} isAnimationActive={false} />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </Card>
      </div>

      {leaders.length > 0 && (
        <Card title={`Who's in the Top ${topN}: ${meta.metrics[tm].label} · ${year}`} subtitle={`All students; ${universeShort(meta, tm, universe)}. Districts to learn from.`}
          name={`top${topN}_${tm}_${year}`} rows={[...leaders, ...extra].map(({ rank, name: n, value }) => ({ rank, district: n, value }))}>
          <div className="overflow-x-auto">
            <table className="data-table tabular">
              <thead><tr><th scope="col">Rank</th><th scope="col" style={{ textAlign: 'left' }}>District</th><th scope="col">{meta.metrics[tm].label}</th><th scope="col">BRRSD gap</th></tr></thead>
              <tbody>
                {[...leaders, ...(extra.length ? [{ gap: true }] : []), ...extra].map((l, i) => l.gap
                  ? <tr key="gap"><td colSpan={4} className="text-muted" style={{ textAlign: 'center' }}>⋯</td></tr>
                  : (
                    <tr key={i} style={DISTRICT_COLOR[l.districtCode] ? { background: 'var(--surface-2)', fontWeight: 600 } : undefined}>
                      <td>{l.rank}</td>
                      <td style={{ textAlign: 'left' }}>
                        {DISTRICT_COLOR[l.districtCode] && <span aria-hidden="true" className="inline-block w-2.5 h-2.5 rounded-full mr-2" style={{ background: DISTRICT_COLOR[l.districtCode] }} />}
                        {l.name}
                      </td>
                      <td>{tf(l.value)}</td>
                      <td>{l.districtCode === TARGET ? '–' : signed(shortfall(meta, tm, get(tm, year, TARGET, ALL), l.value) == null ? null
                        : -shortfall(meta, tm, get(tm, year, TARGET, ALL), l.value))}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------------- */
/* View 2: Neighbor recovery benchmark                                        */
/* ------------------------------------------------------------------------- */
function Recovery({ data }) {
  const [subject, setSubject] = useState('Math')
  const [group, setGroup] = useState(ALL)
  const districts = orderedDistricts(data.meta)
  const name = (d) => data.meta.districts[d].short
  const years = data.meta.years

  const rows = useMemo(() => years.map((year) => {
    const row = { year }
    for (const d of districts) {
      row[name(d)] = first(data.proficiency, { year, districtCode: d, schoolCode: null, subject, test: 'All', group })?.rate ?? null
    }
    return row
  }), [data, subject, group]) // eslint-disable-line react-hooks/exhaustive-deps

  const metrics = useMemo(() => districts.map((d) => {
    const series = where(data.proficiency, { districtCode: d, schoolCode: null, subject, test: 'All', group })
      .sort((a, b) => a.year.localeCompare(b.year))
    const at = (y) => series.find((r) => r.year === y)?.rate ?? null
    const latest = series.filter((r) => r.year >= RECOVERY_START).pop()
    const trough = at(RECOVERY_START)
    const baseline = at(BASELINE)
    const span = latest ? Number(latest.year.slice(0, 4)) - Number(RECOVERY_START.slice(0, 4)) : 0
    const velocity = latest && trough != null && span > 0 ? (latest.rate - trough) / span : null
    const lost = baseline != null && trough != null ? baseline - trough : null
    return {
      district: name(d), districtCode: d, subject, group, baseline2019: baseline, trough2022: trough,
      latestYear: latest?.year ?? null, latest: latest?.rate ?? null,
      velocityPtsPerYear: velocity == null ? null : Number(velocity.toFixed(2)),
      netVsBaseline: latest && baseline != null ? Number((latest.rate - baseline).toFixed(1)) : null,
      pctOfLossRecovered: lost > 0 && latest ? Math.round(((latest.rate - trough) / lost) * 100) : null,
    }
  }), [data, subject, group]) // eslint-disable-line react-hooks/exhaustive-deps

  const brrsd = metrics.find((m) => m.districtCode === TARGET)
  // K-12 peers only: Watchung Hills (9-12) tests a different set of grades.
  const peers = metrics.filter((m) => m.districtCode !== TARGET && m.velocityPtsPerYear != null && data.meta.districts[m.districtCode].grades !== '09-12')
  const peerAvg = peers.length ? peers.reduce((s, m) => s + m.velocityPtsPerYear, 0) / peers.length : null

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap gap-4">
        <Control label="Subject" value={subject} onChange={setSubject} options={['ELA', 'Math', 'Science']} />
        <Control label="Student group" value={group} onChange={setGroup} options={GROUPS} />
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat label={`BRRSD ${subject} · ${brrsd?.latestYear ?? ''}`} value={fmtPct(brrsd?.latest)}
          delta={brrsd?.netVsBaseline} deltaLabel={`vs ${BASELINE}`} />
        <Stat label="BRRSD recovery velocity" value={brrsd?.velocityPtsPerYear == null ? '–' : `${signed(brrsd.velocityPtsPerYear, '')}`}
          note={`pts per year since ${RECOVERY_START}`} />
        <Stat label="Peer average velocity" value={peerAvg == null ? '–' : signed(peerAvg, '')}
          note={`pts per year · K-12 peers: ${peers.map((p) => p.district).join(', ')}`} />
        <Stat label="Share of pandemic loss recovered" value={brrsd?.pctOfLossRecovered == null ? '–' : `${brrsd.pctOfLossRecovered}%`}
          note={`(latest − ${RECOVERY_START}) ÷ (${BASELINE} − ${RECOVERY_START})`} />
      </div>
      <Card title={`${subject} proficiency: Bridgewater-Raritan vs. neighbors`} subtitle={`${group}; % meeting or exceeding expectations`}
        name={`recovery_${subject.toLowerCase()}_trend`} rows={rows}
        table={<DataTable rows={rows} columns={[{ key: 'year', label: 'Year' },
          ...districts.map((d) => ({ key: name(d), label: name(d), render: fmtPct }))]} />}
        footnote="Watchung Hills Regional serves grades 9-12 only, so its rates reflect high-school tests (e.g., Algebra I/Geometry, Grade 9-10 ELA) and are not directly comparable to K-12 districts.">
        <ResponsiveContainer width="100%" height={340}>
          <LineChart data={rows} margin={{ top: 20, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid {...gridProps} />
            <XAxis dataKey="year" {...axisProps} />
            <YAxis {...pctAxis} />
            <Tooltip formatter={tooltipFmt} />
            <Legend />
            {waiverBand(years)}
            <ReferenceLine x={RECOVERY_START} stroke="var(--text-muted)" strokeDasharray="4 4"
              label={{ value: 'Recovery starts', position: 'insideBottomLeft', fill: 'var(--text-muted)', fontSize: 11 }} />
            {districts.map((d) => (
              <Line key={d} dataKey={name(d)} stroke={DISTRICT_COLOR[d]} strokeWidth={d === TARGET ? 3 : 2}
                dot={{ r: d === TARGET ? 5 : 4 }} connectNulls={false} isAnimationActive={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </Card>
      <Card title="Recovery velocity since 2021-22" subtitle="Average change in proficiency, percentage points per year"
        name={`recovery_${subject.toLowerCase()}_metrics`} rows={metrics}
        table={<DataTable rows={metrics} columns={[
          { key: 'district', label: 'District' }, { key: 'baseline2019', label: BASELINE, render: fmtPct },
          { key: 'trough2022', label: RECOVERY_START, render: fmtPct }, { key: 'latest', label: 'Latest', render: fmtPct },
          { key: 'velocityPtsPerYear', label: 'Pts / yr', render: (v) => signed(v, '') },
          { key: 'netVsBaseline', label: 'Net vs 2018-19', render: (v) => signed(v) },
          { key: 'pctOfLossRecovered', label: '% of loss recovered', render: (v) => (v == null ? '–' : `${v}%`) },
        ]} />}>
        <ResponsiveContainer width="100%" height={240}>
          <BarChart data={metrics} layout="vertical" margin={{ top: 4, right: 48, left: 8, bottom: 0 }} barCategoryGap={6}>
            <CartesianGrid {...gridProps} horizontal={false} vertical />
            <XAxis type="number" {...axisProps} tickFormatter={(v) => `${v}`} />
            <YAxis type="category" dataKey="district" {...axisProps} width={120} />
            <ReferenceLine x={0} stroke="var(--text-muted)" />
            <Tooltip formatter={(v) => `${signed(v, ' pts/yr')}`} />
            <Bar dataKey="velocityPtsPerYear" name="Pts per year" radius={[0, 4, 4, 0]} isAnimationActive={false}>
              {metrics.map((m) => <Cell key={m.districtCode} fill={DISTRICT_COLOR[m.districtCode]} />)}
              <LabelList dataKey="velocityPtsPerYear" position="right" formatter={(v) => signed(v, '')}
                style={{ fill: 'var(--text-secondary)', fontSize: 12 }} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Card>
    </div>
  )
}

/* ------------------------------------------------------------------------- */
/* View 3: Growth (mSGP)                                                       */
/* ------------------------------------------------------------------------- */
function Growth({ data }) {
  const median = data.meta.statewideMsgpMedian ?? 50
  const gradeYears = [...new Set(where(data.growth, { districtCode: TARGET, schoolCode: null, group: ALL })
    .filter((r) => r.grade !== 'All').map((r) => r.year))].sort()
  const [year, setYear] = useState(gradeYears[gradeYears.length - 1])
  const [subject, setSubject] = useState('Math')
  const districts = orderedDistricts(data.meta).filter((d) => data.meta.districts[d].grades !== '09-12')
  const name = (d) => data.meta.districts[d].short

  const byGrade = ['Grade 4', 'Grade 5', 'Grade 6', 'Grade 7', 'Grade 8'].map((grade) => {
    const g = (s) => first(data.growth, { year, districtCode: TARGET, schoolCode: null, subject: s, grade, group: ALL })?.msgp ?? null
    return { grade, ELA: g('ELA'), Math: g('Math') }
  })
  const below = byGrade.flatMap((r) => ['ELA', 'Math'].filter((s) => r[s] != null && r[s] < median).map((s) => `${r.grade} ${s} (${r[s]})`))

  // Keep years without mSGP on the axis so the 2019-20 to 2021-22 gap stays visible.
  const growthYears = where(data.growth, { grade: 'All', group: ALL, schoolCode: null }).map((r) => r.year).sort()
  const trendYears = data.meta.years.filter((y) => y >= growthYears[0])
  const trend = trendYears.map((y) => {
    const row = { year: y }
    for (const d of districts) row[name(d)] = first(data.growth, { year: y, districtCode: d, schoolCode: null, subject, grade: 'All', group: ALL })?.msgp ?? null
    return row
  })
  const subgroupRows = GROUPS.map((group) => {
    const g = (s) => first(data.growth, { year, districtCode: TARGET, schoolCode: null, subject: s, grade: 'All', group })
    return { group, ELA: g('ELA')?.msgp ?? null, 'ELA (state)': g('ELA')?.stateMsgp ?? null, Math: g('Math')?.msgp ?? null, 'Math (state)': g('Math')?.stateMsgp ?? null }
  })
  const flag = (v) => (v == null ? '–' : v < median
    ? <span style={{ color: 'var(--critical-ink)' }} className="font-semibold">▼ {fmt(v)} <span className="text-xs font-normal">below median</span></span>
    : fmt(v))
  const BelowMark = (props) => {
    const { x, y, width, value } = props
    if (value == null || value >= median) return null
    return <text x={x + width / 2} y={y - 6} textAnchor="middle" fontSize={12} fill="var(--critical)" aria-label="below state median">▼</text>
  }

  return (
    <div className="space-y-5">
      <div className="card p-4 text-sm text-secondary leading-relaxed">
        <strong className="text-[color:var(--text-primary)]">Why growth matters:</strong> a median Student Growth Percentile compares
        each student's progress with students statewide who started at a similar score. It is largely independent of demographics
        and starting achievement. <strong>50 is the statewide median</strong>: below 50 means typical BRRSD students grew less than
        academic peers elsewhere. NJDOE did not compute mSGP for 2019-20 through 2021-22.
      </div>
      <div className="flex flex-wrap gap-4">
        <Control label="Year" value={year} onChange={setYear} options={gradeYears} />
      </div>
      <Card title={`mSGP by grade · ${year}`} subtitle={`Bridgewater-Raritan, all students. ▼ marks grades below the ${median} state median.`}
        name={`msgp_by_grade_${year}`} rows={byGrade}
        table={<DataTable rows={byGrade} columns={[{ key: 'grade', label: 'Grade' },
          { key: 'ELA', label: 'ELA mSGP', render: flag }, { key: 'Math', label: 'Math mSGP', render: flag }]} />}
        footnote={below.length ? `Below the state median in ${year}: ${below.join('; ')}.` : `No grade fell below the state median in ${year}.`}>
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={byGrade} margin={{ top: 24, right: 16, left: 0, bottom: 0 }} barGap={2} barCategoryGap="28%">
            <CartesianGrid {...gridProps} />
            <XAxis dataKey="grade" {...axisProps} />
            <YAxis {...axisProps} domain={[0, 100]} width={36} />
            <Tooltip formatter={(v) => (v < median ? `${v} (below state median)` : v)} />
            <Legend />
            <ReferenceLine y={median} stroke="var(--text-primary)" strokeDasharray="5 4"
              label={{ value: `State median ${median}`, position: 'insideTopRight', fill: 'var(--text-secondary)', fontSize: 11 }} />
            {['ELA', 'Math'].map((s) => (
              <Bar key={s} dataKey={s} fill={SUBJECT_COLOR[s]} radius={[4, 4, 0, 0]} isAnimationActive={false}>
                <LabelList dataKey={s} content={<BelowMark />} />
              </Bar>
            ))}
          </BarChart>
        </ResponsiveContainer>
      </Card>
      <Card title={`Districtwide ${subject} mSGP over time`} subtitle="K-8 districts only (Watchung Hills is grades 9-12, where mSGP is not reported)"
        name={`msgp_trend_${subject.toLowerCase()}`} rows={trend}
        table={<DataTable rows={trend} columns={[{ key: 'year', label: 'Year' }, ...districts.map((d) => ({ key: name(d), label: name(d), render: flag }))]} />}>
        <div className="mb-3"><Control label="Subject" value={subject} onChange={setSubject} options={['ELA', 'Math']} /></div>
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={trend} margin={{ top: 16, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid {...gridProps} />
            <XAxis dataKey="year" {...axisProps} />
            <YAxis {...axisProps} domain={[30, 70]} width={36} />
            <Tooltip />
            <Legend />
            {waiverBand(trendYears)}
            <ReferenceArea y1={30} y2={median} fill="var(--band)" stroke="none" />
            <ReferenceLine y={median} stroke="var(--text-primary)" strokeDasharray="5 4"
              label={{ value: 'State median', position: 'insideBottomRight', fill: 'var(--text-secondary)', fontSize: 11 }} />
            {districts.map((d) => (
              <Line key={d} dataKey={name(d)} stroke={DISTRICT_COLOR[d]} strokeWidth={d === TARGET ? 3 : 2} dot={{ r: 4 }}
                connectNulls={false} isAnimationActive={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </Card>
      <Card title={`mSGP by student group · ${year}`} subtitle="District median vs. the statewide median for the same group"
        name={`msgp_subgroups_${year}`} rows={subgroupRows}>
        <div className="overflow-x-auto">
          <DataTable rows={subgroupRows} columns={[{ key: 'group', label: 'Group' },
            { key: 'ELA', label: 'ELA', render: flag }, { key: 'ELA (state)', label: 'ELA state', render: (v) => fmt(v) },
            { key: 'Math', label: 'Math', render: flag }, { key: 'Math (state)', label: 'Math state', render: (v) => fmt(v) }]} />
        </div>
      </Card>
    </div>
  )
}

/* ------------------------------------------------------------------------- */
/* View 4: School-by-school & Algebra I                                       */
/* ------------------------------------------------------------------------- */
// Diverging scale around a goal: red = below it, neutral = at it, green = above it.
// The number is always printed in the cell, so color is never the only cue.
const DIV_STEPS = ['--div-n3', '--div-n2', '--div-n1', '--div-0', '--div-p1', '--div-p2', '--div-p3']
const DIV_EDGES = [-15, -7.5, -2.5, 2.5, 7.5, 15]
const DIV_LABELS = ['15+ below', '7.5-15 below', '2.5-7.5 below', 'within 2.5', '2.5-7.5 above', '7.5-15 above', '15+ above']
function divStyle(gap) {
  if (gap == null) return {}
  let i = 0
  while (i < DIV_EDGES.length && gap >= DIV_EDGES[i]) i++
  return { background: `var(${DIV_STEPS[i]})`, color: i === 0 || i === 6 ? 'var(--div-ink-strong)' : 'var(--div-ink)' }
}
function DivLegend({ goal }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted mb-3" aria-label={`Color key: points relative to the ${goal}`}>
      <span>Points vs. {goal}:</span>
      {DIV_STEPS.map((v, i) => (
        <span key={v} className="inline-flex items-center gap-1">
          <span className="inline-block w-3.5 h-3.5 rounded-sm" style={{ background: `var(${v})`, border: '1px solid var(--border)' }} />{DIV_LABELS[i]}
        </span>
      ))}
    </div>
  )
}

function Schools({ data }) {
  const schools = data.meta.schools
  const [subject, setSubject] = useState('ELA')
  const [group, setGroup] = useState(ALL)
  const [goal, setGoal] = useState('top20')
  const bench = useBench(data)
  const topN = data.meta.benchmarks?.topN ?? 20
  // Goal for a year: the Top-20 district cutoff (K-12 districts), else NJDOE's state rate.
  const goalOf = (y, stateRate) => (goal === 'top20' ? bench(y, SUBJECT_METRIC[subject], group)?.top20 ?? stateRate ?? null : stateRate ?? null)
  const stateFallback = (y) => goal === 'top20' && !bench(y, SUBJECT_METRIC[subject], group)
  const goalLabel = goal === 'top20' ? `Top ${topN} district cutoff` : 'state average'
  const years = data.meta.years.filter((y) => !WAIVER_YEARS.includes(y))
  const codeOf = (k) => k.split('-').pop()
  const schoolList = Object.entries(schools).sort((a, b) => a[1].localeCompare(b[1]))

  const matrix = schoolList.map(([key, sname]) => {
    const row = { school: sname, schoolCode: codeOf(key) }
    for (const y of years) {
      const r = first(data.proficiency, { year: y, districtCode: TARGET, schoolCode: codeOf(key), subject, test: 'All', group })
      row[y] = r?.rate ?? null
      row[`${y} goal`] = goalOf(y, r?.stateRate)
    }
    const ab = where(data.absenteeism, { districtCode: TARGET, schoolCode: codeOf(key), group: ALL }).sort((a, b) => a.year.localeCompare(b.year)).pop()
    row.chronicAbsenteeism = ab?.rate ?? null
    row.chronicAbsenteeismYear = ab?.year ?? null
    return row
  }).filter((r) => years.some((y) => r[y] != null) || r.chronicAbsenteeism != null)
  const shownYears = years.filter((y) => matrix.some((r) => r[y] != null))
  const districtRec = (y) => first(data.proficiency, { year: y, districtCode: TARGET, schoolCode: null, subject, test: 'All', group })
  const district = Object.fromEntries(shownYears.map((y) => [y, districtRec(y)?.rate ?? null]))
  const districtGoal = Object.fromEntries(shownYears.map((y) => [y, goalOf(y, districtRec(y)?.stateRate)]))

  // Algebra I
  // Charts keep the waiver years (as gaps); the heat table drops them.
  const chartYears = data.meta.years
  const alg = chartYears.map((y) => {
    const row = { year: y }
    for (const g of GROUPS) row[g] = first(data.proficiency, { year: y, districtCode: TARGET, schoolCode: null, subject: 'Math', test: 'Algebra I', group: g })?.rate ?? null
    row['State (all)'] = first(data.proficiency, { year: y, districtCode: TARGET, schoolCode: null, subject: 'Math', test: 'Algebra I', group: ALL })?.stateRate ?? null
    return row
  })
  const districts = orderedDistricts(data.meta)
  const name = (d) => data.meta.districts[d].short
  const mid = chartYears.map((y) => {
    const row = { year: y }
    for (const d of districts) {
      if (data.meta.districts[d].grades === '09-12') continue
      row[name(d)] = first(data.proficiency, { year: y, districtCode: d, schoolCode: null, subject: 'Math', test: 'Grades 5-8', group })?.rate ?? null
    }
    return row
  })
  const algRaw = where(data.proficiency, { districtCode: TARGET, test: 'Algebra I' })

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap gap-4">
        <Control label="Subject" value={subject} onChange={setSubject} options={['ELA', 'Math', 'Science']} />
        <Control label="Student group" value={group} onChange={setGroup} options={GROUPS} />
        <Control label="Color against" value={goal} onChange={setGoal}
          options={[{ value: 'top20', label: `Top ${topN} district cutoff` }, { value: 'state', label: 'State average' }]} />
      </div>
      <Card title={`School-by-school ${subject} proficiency`} subtitle={`${group}; % meeting or exceeding expectations. Each cell is colored by how far it sits from the ${goalLabel} for that year.`}
        name={`schools_${subject.toLowerCase()}_${group.replace(/\s+/g, '_').toLowerCase()}`} rows={matrix}
        footnote={goal === 'top20'
          ? `The Top ${topN} cutoff is the districtwide ${subject} rate of the ${topN}th-best NJ K-12 district (all tested grades), a stretch goal for each school. Years marked * have no statewide ranking (or none for this group), so they're compared with the state average where NJDOE published one; 2014-15 and 2015-16 files have no all-grades state rate, so those columns are left uncolored. "–" = not tested or suppressed (n<10).`
          : 'Compared with NJDOE\'s statewide rate for the same school year and group. "–" = not tested or suppressed (n<10).'}>
        <DivLegend goal={goalLabel} />
        <div className="overflow-x-auto">
          <table className="data-table tabular">
            <thead>
              <tr>
                <th scope="col">School</th>
                {shownYears.map((y) => <th key={y} scope="col" title={stateFallback(y) ? 'Colored against the state average' : undefined}>{y}{stateFallback(y) ? '*' : ''}</th>)}
                <th scope="col">Chronic absent.</th>
              </tr>
            </thead>
            <tbody>
              {matrix.map((r) => (
                <tr key={r.schoolCode}>
                  <td className="whitespace-nowrap">{r.school}</td>
                  {shownYears.map((y) => (
                    <td key={y} title={`${r.school} · ${y}: ${fmtPct(r[y])}${r[`${y} goal`] != null ? ` (goal ${fmtPct(r[`${y} goal`])}, ${signed(r[y] - r[`${y} goal`])})` : ''}`}
                      style={divStyle(r[y] != null && r[`${y} goal`] != null ? r[y] - r[`${y} goal`] : null)}>
                      {fmt(r[y], 0)}
                    </td>
                  ))}
                  <td title={r.chronicAbsenteeismYear ?? ''}>{fmtPct(r.chronicAbsenteeism)}</td>
                </tr>
              ))}
              <tr>
                <td className="font-semibold">District</td>
                {shownYears.map((y) => (
                  <td key={y} className="font-semibold" style={divStyle(district[y] != null && districtGoal[y] != null ? district[y] - districtGoal[y] : null)}>
                    {fmt(district[y], 0)}</td>))}
              </tr>
              <tr>
                <td className="text-muted">Goal ({goal === 'top20' ? `Top ${topN}` : 'state'})</td>
                {shownYears.map((y) => <td key={y} className="text-muted">{fmt(districtGoal[y], 0)}</td>)}
                <td />
              </tr>
            </tbody>
          </table>
        </div>
      </Card>
      <Card title="Algebra I proficiency by student group" subtitle="Bridgewater-Raritan, all grades taking the Algebra I end-of-course test"
        name="brrsd_algebra1" rows={algRaw}
        table={<DataTable rows={alg} columns={[{ key: 'year', label: 'Year' }, ...GROUPS.map((g) => ({ key: g, label: g, render: fmtPct })),
          { key: 'State (all)', label: 'State', render: fmtPct }]} />}
        footnote="Algebra I is taken by advanced 7th/8th graders as well as high-school students, so year-to-year changes can reflect who sits the test (e.g., course-placement policy) as much as instruction.">
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={alg} margin={{ top: 16, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid {...gridProps} />
            <XAxis dataKey="year" {...axisProps} />
            <YAxis {...pctAxis} />
            <Tooltip formatter={tooltipFmt} />
            <Legend />
            {waiverBand(chartYears)}
            {GROUPS.map((g) => <Line key={g} dataKey={g} stroke={GROUP_COLOR[g]} strokeWidth={2} dot={{ r: 4 }} isAnimationActive={false} />)}
            <Line dataKey="State (all)" stroke="var(--neutral-series)" strokeDasharray="5 4" strokeWidth={1.5} dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </Card>
      <Card title="Grades 5-8 Math (weighted) vs. neighbors" subtitle={`${group}; grade-level math tests 5-8 combined, weighted by test takers`}
        name="grades5to8_math" rows={mid}
        table={<DataTable rows={mid} columns={[{ key: 'year', label: 'Year' }, ...districts.filter((d) => data.meta.districts[d].grades !== '09-12').map((d) => ({ key: name(d), label: name(d), render: fmtPct }))]} />}
        footnote="Students who take Algebra I in middle school are excluded from the grade-level test, which lowers grade 7-8 rates in districts that accelerate many students.">
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={mid} margin={{ top: 16, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid {...gridProps} />
            <XAxis dataKey="year" {...axisProps} />
            <YAxis {...pctAxis} />
            <Tooltip formatter={tooltipFmt} />
            <Legend />
            {waiverBand(chartYears)}
            {districts.filter((d) => data.meta.districts[d].grades !== '09-12').map((d) => (
              <Line key={d} dataKey={name(d)} stroke={DISTRICT_COLOR[d]} strokeWidth={d === TARGET ? 3 : 2} dot={{ r: 4 }} isAnimationActive={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </Card>
    </div>
  )
}

/* ------------------------------------------------------------------------- */
/* View 5: Budget & staffing                                                  */
/* ------------------------------------------------------------------------- */
const STAGE_LABEL = { audited: 'actual', 'revised budget': 'revised budget', 'proposed budget': 'proposed budget' }
const BUDGET_LINES = ['regularInstruction', 'specialEdInstruction', 'tuition', 'relatedServices', 'childStudyTeams', 'guidance',
  'libraryMedia', 'healthServices', 'schoolAdministration', 'generalAdministration', 'centralServices', 'plantOperations',
  'transportation', 'athletics', 'employeeBenefits', 'debtService']

function Budget({ data, fiscal }) {
  const staff = where(data.staffing, { districtCode: TARGET, schoolCode: null }).sort((a, b) => a.year.localeCompare(b.year))
  const fy = fiscal?.years ?? {}
  const budgetRows = where(data.budget, { districtCode: TARGET })
  const bud = (y) => budgetRows.find((r) => r.year === y)
  const spendOf = (y, m) => (data.spending ?? []).find((r) => r.year === y && r.entity === TARGET && r.metric === m)?.value ?? null
  const years = [...new Set([...staff.map((s) => s.year), ...budgetRows.map((b) => b.year), ...Object.keys(fy)])].sort()
  // NJDOE figures, unless fiscal_overrides.json sets a value for that year.
  const pickF = (f, key, auto) => (f[key] ?? auto ?? null)
  const rows = years.map((y) => {
    const s = staff.find((r) => r.year === y) ?? {}
    const f = fy[y] ?? {}
    const b = bud(y) ?? {}
    return {
      year: y, enrollment: s.enrollment ?? null, teachers: s.teachers ?? null, administrators: s.administrators ?? null,
      teachersEstimated: s.teachersEstimated ?? false, administratorsEstimated: s.administratorsEstimated ?? false,
      studentTeacherRatio: s.studentTeacherRatio ?? null, studentAdminRatio: s.studentAdminRatio ?? null,
      teachersPerAdmin: s.teachersPerAdmin ?? null,
      budgetStage: b.stage ? STAGE_LABEL[b.stage] : null,
      totalOperatingBudget: pickF(f, 'totalOperatingBudget', b.totalOperatingBudget),
      generalFundTaxLevy: b.generalFundTaxLevy ?? null, stateAid: b.stateAid ?? null, employeeBenefits: b.employeeBenefits ?? null,
      adminCostPerPupil: pickF(f, 'adminCostPerPupil', spendOf(y, 'admin')),
      budgetedCostPerPupil: pickF(f, 'budgetedCostPerPupil', spendOf(y, 'budgetaryCost')),
    }
  })
  const hasFiscal = rows.some((r) => r.totalOperatingBudget != null || r.adminCostPerPupil != null)
  const latestB = [...budgetRows].sort((a, z) => a.year.localeCompare(z.year)).pop()
  const prevB = latestB && budgetRows.find((r) => r.year === years[years.indexOf(latestB.year) - 1])
  const pctChange = (k) => (latestB?.[k] && prevB?.[k] ? ((latestB[k] - prevB[k]) / prevB[k]) * 100 : null)
  const fields = data.meta.budget?.fields ?? {}
  const lineYears = budgetRows.map((r) => r.year).sort()
  const lineRows = BUDGET_LINES.filter((k) => budgetRows.some((r) => r[k] != null)).map((k) => {
    const o = { line: fields[k] ?? k, key: k }
    for (const y of lineYears) o[y] = bud(y)?.[k] ?? null
    const a = bud(lineYears[0])?.[k], z = bud(lineYears[lineYears.length - 1])?.[k]
    o.change = a && z ? Math.round(((z - a) / a) * 100) : null
    return o
  })
  const taxRows = budgetRows.filter((r) => r.taxRates?.length).flatMap((r) => r.taxRates.map((t) => ({ year: r.year, ...t })))
  const munis = [...new Set(taxRows.map((t) => t.municipality))]
  const taxTable = [...new Set(taxRows.map((t) => t.year))].sort().map((y) => ({
    year: y, ...Object.fromEntries(munis.map((m) => [m, taxRows.find((t) => t.year === y && t.municipality === m)?.rate ?? null])),
  }))
  // Indexed overlay: every measure relative to its own first available year = 100 (one shared axis, no dual scale).
  const measures = [
    { key: 'teachers', label: 'Classroom teachers', color: 'var(--series-1)' },
    { key: 'administrators', label: 'Administrators', color: 'var(--series-2)' },
    { key: 'enrollment', label: 'Enrollment', color: 'var(--series-3)' },
    { key: 'totalOperatingBudget', label: 'Operating budget', color: 'var(--series-4)' },
    { key: 'adminCostPerPupil', label: 'Admin cost / pupil', color: 'var(--series-5)' },
  ].filter((m) => rows.some((r) => r[m.key] != null))
  const baseYear = rows.find((r) => measures.every((m) => r[m.key] != null))?.year ?? rows.find((r) => r.teachers != null)?.year
  const baseRow = rows.find((r) => r.year === baseYear) ?? {}
  const indexed = rows.map((r) => {
    const o = { year: r.year }
    for (const m of measures) o[m.label] = r[m.key] != null && baseRow[m.key] ? Number(((r[m.key] / baseRow[m.key]) * 100).toFixed(1)) : null
    return o
  }).filter((r) => measures.some((m) => r[m.label] != null))

  const districts = orderedDistricts(data.meta)
  const latestYear = [...new Set(where(data.staffing, { schoolCode: null }).filter((r) => r.studentTeacherRatio).map((r) => r.year))].sort().pop()
  const peerRows = districts.map((d) => {
    const s = first(data.staffing, { year: latestYear, districtCode: d, schoolCode: null }) ?? {}
    return { district: data.meta.districts[d].short, districtCode: d, year: latestYear, studentTeacherRatio: s.studentTeacherRatio ?? null,
      studentAdminRatio: s.studentAdminRatio ?? null, teachers: s.teachers ?? null, administrators: s.administrators ?? null, teachersPerAdmin: s.teachersPerAdmin ?? null }
  })

  return (
    <div className="space-y-5">
      {!hasFiscal && (
        <div className="card p-4 text-sm text-secondary leading-relaxed" role="note">
          <strong className="text-[color:var(--text-primary)]">Budget figures not available.</strong> NJDOE's User-Friendly Budget
          files could not be loaded this time; they are retried on the next monthly update. Staffing data below comes from NJDOE.
        </div>
      )}
      {latestB && (
        <>
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-lg font-semibold">{latestB.year} budget <span className="chip ml-1">{STAGE_LABEL[latestB.stage]}</span></h2>
            <a className="text-xs underline text-muted" href={data.meta.budget.source}>NJDOE User-Friendly Budget summaries</a>
          </div>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            {[['totalOperatingBudget', 'Operating budget'], ['generalFundTaxLevy', 'School tax levy'], ['stateAid', 'State aid'], ['employeeBenefits', 'Employee benefits']].map(([k, label]) => (
              <Stat key={k} label={label} value={money(latestB[k])} neutral unit="%"
                delta={pctChange(k)} deltaLabel={prevB ? `vs ${prevB.year} ${STAGE_LABEL[prevB.stage]}` : ''} />
            ))}
          </div>
        </>
      )}
      <Card title="Staffing, enrollment & budget, indexed" subtitle={`Each line = 100 in ${baseYear ?? 'the first year'}; one shared scale so different units can be compared without a misleading dual axis`}
        name="brrsd_staffing_budget" rows={rows}
        table={<DataTable rows={rows} columns={[
          { key: 'year', label: 'Year' }, { key: 'enrollment', label: 'Enrollment', render: (v) => (v == null ? '–' : v.toLocaleString()) },
          { key: 'teachers', label: 'Teachers', render: (v, r) => (v == null ? '–' : `${v}${r.teachersEstimated ? '*' : ''}`) },
          { key: 'administrators', label: 'Admins', render: (v, r) => (v == null ? '–' : `${v}${r.administratorsEstimated ? '*' : ''}`) },
          { key: 'studentTeacherRatio', label: 'Students / teacher', render: (v) => fmt(v, 0) },
          { key: 'studentAdminRatio', label: 'Students / admin', render: (v) => fmt(v, 0) },
          { key: 'totalOperatingBudget', label: 'Operating budget', render: money },
          { key: 'budgetStage', label: 'Budget basis', render: (v) => v ?? '–' },
          { key: 'budgetedCostPerPupil', label: 'Budgetary cost / pupil', render: money },
          { key: 'adminCostPerPupil', label: 'Admin $ / pupil', render: money },
        ]} />}
        footnote="* Estimated as enrollment ÷ NJDOE's published student-to-staff ratio for years that did not publish headcounts. Staff counts are NJDOE full-time-equivalent figures as of October of the school year. Budget figures come from NJDOE's User-Friendly Budget summaries: audited actuals where available, otherwise the revised or proposed budget (see 'Budget basis' in the table). Admin $ / pupil is NJDOE's budgetary-cost administration figure. Values in public/data/fiscal_overrides.json, if any, take precedence.">
        <ResponsiveContainer width="100%" height={320}>
          <LineChart data={indexed} margin={{ top: 16, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid {...gridProps} />
            <XAxis dataKey="year" {...axisProps} />
            <YAxis {...axisProps} width={40} domain={['auto', 'auto']} />
            <Tooltip formatter={(v) => `${fmt(v)} (index)`} />
            <Legend />
            <ReferenceLine y={100} stroke="var(--text-muted)" strokeDasharray="4 4" />
            {measures.map((m) => <Line key={m.key} dataKey={m.label} stroke={m.color} strokeWidth={2} dot={{ r: 4 }} connectNulls isAnimationActive={false} />)}
          </LineChart>
        </ResponsiveContainer>
      </Card>
      <div className="grid lg:grid-cols-2 gap-5">
        <Card title="Teachers vs. administrators" subtitle="Bridgewater-Raritan headcount (NJDOE)" name="brrsd_headcount" rows={rows.filter((r) => r.teachers != null)}>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={rows.filter((r) => r.teachers != null)} margin={{ top: 16, right: 8, left: 0, bottom: 0 }} barGap={2}>
              <CartesianGrid {...gridProps} />
              <XAxis dataKey="year" {...axisProps} />
              <YAxis {...axisProps} width={40} />
              <Tooltip />
              <Legend />
              <Bar dataKey="teachers" name="Teachers" fill="var(--series-1)" radius={[4, 4, 0, 0]} isAnimationActive={false} />
              <Bar dataKey="administrators" name="Administrators" fill="var(--series-2)" radius={[4, 4, 0, 0]} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </Card>
        <Card title={`Students per administrator · ${latestYear ?? ''}`} subtitle="Higher = leaner central/building administration"
          name="peer_staff_ratios" rows={peerRows}
          table={<DataTable rows={peerRows} columns={[{ key: 'district', label: 'District' },
            { key: 'studentTeacherRatio', label: 'Students / teacher', render: (v) => fmt(v, 0) },
            { key: 'studentAdminRatio', label: 'Students / admin', render: (v) => fmt(v, 0) },
            { key: 'teachers', label: 'Teachers', render: (v) => v ?? '–' }, { key: 'administrators', label: 'Admins', render: (v) => v ?? '–' }]} />}>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={peerRows} layout="vertical" margin={{ top: 4, right: 48, left: 8, bottom: 0 }}>
              <CartesianGrid {...gridProps} horizontal={false} vertical />
              <XAxis type="number" {...axisProps} />
              <YAxis type="category" dataKey="district" {...axisProps} width={120} />
              <Tooltip />
              <Bar dataKey="studentAdminRatio" name="Students per admin" radius={[0, 4, 4, 0]} isAnimationActive={false}>
                {peerRows.map((r) => <Cell key={r.districtCode} fill={DISTRICT_COLOR[r.districtCode]} />)}
                <LabelList dataKey="studentAdminRatio" position="right" style={{ fill: 'var(--text-secondary)', fontSize: 12 }} />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Card>
      </div>
      {lineRows.length > 0 && (
        <Card title="Selected budget lines" subtitle={`Bridgewater-Raritan, whole-district dollars. ${lineYears.filter((y) => bud(y)?.basis === 'budgeted').map((y) => `${y} = ${STAGE_LABEL[bud(y).stage]}`).join('; ')}; earlier years are audited actuals.`}
          name="brrsd_budget_lines" rows={lineRows.map(({ key, ...r }) => r)}
          footnote="Each line is the NJDOE 'Total' for that budget function in the general fund (debt service is its own fund). Employee benefits are budgeted centrally, so the lines above them are mostly salaries, supplies and services.">
          <div className="overflow-x-auto">
            <DataTable rows={lineRows} columns={[{ key: 'line', label: 'Budget line' },
              ...lineYears.map((y) => ({ key: y, label: `${y}${bud(y)?.basis === 'budgeted' ? '*' : ''}`, render: money })),
              { key: 'change', label: `Change since ${lineYears[0]}`, render: (v) => (v == null ? '–' : `${v > 0 ? '+' : ''}${v}%`) }]} />
          </div>
        </Card>
      )}
      {taxTable.length > 0 && (
        <Card title="Estimated school tax rates" subtitle="General-fund school tax rate per $100 of assessed value, set with each year's proposed budget"
          name="brrsd_school_tax_rates" rows={taxTable}
          footnote="Rates differ by town because each municipality's share of the levy is apportioned by equalized property value, while assessments are on each town's own basis. Equalized tax rates are included in brrsdData.json.">
          <div className="overflow-x-auto">
            <DataTable rows={taxTable} columns={[{ key: 'year', label: 'Year' }, ...munis.map((m) => ({ key: m, label: m, render: (v) => (v == null ? '–' : `$${v.toFixed(3)}`) }))]} />
          </div>
        </Card>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------------- */
/* View 6: Spending vs. peers (NJDOE Taxpayers' Guide to Education Spending)  */
/* ------------------------------------------------------------------------- */
const STATE = 'STATE_AVG'
const GROUP = 'GROUP_AVG:G'
const TOP20 = 'TOP20_MEDIAN'
// Variance table: what each line costs vs. neighbors, the Top 20 achievers and the state.
const VARIANCE_CATS = [
  ['budgetaryCost', false], ['classroom', false], ['support', false], ['admin', false], ['admin', true], ['operations', false],
  ['extracurricular', false], ['legal', false], ['benefitsPctSalaries', false], ['medianTeacherSalary', false],
  ['studentTeacherRatio', false], ['studentAdminRatio', false], ['medianAdminSalary', false], ['totalSpending', false],
]
const SPEND_CATS = ['classroom', 'support', 'admin', 'operations', 'extracurricular', 'classroomSupplies']
const STAFF_CATS = ['studentTeacherRatio', 'studentSupportRatio', 'studentAdminRatio', 'medianTeacherSalary', 'medianSupportSalary', 'medianAdminSalary']
const TABLE_CATS = ['budgetaryCost', 'classroom', 'classroomSalBen', 'classroomSupplies', 'classroomPurchased', 'support', 'supportSalBen',
  'admin', 'adminSalBen', 'legal', 'operations', 'operationsSalBen', 'extracurricular', 'equipment', 'benefitsPctSalaries',
  'studentTeacherRatio', 'medianTeacherSalary', 'studentSupportRatio', 'medianSupportSalary', 'studentAdminRatio', 'medianAdminSalary',
  'facultyAdminRatio', 'totalSpending']
const SHORT = {
  classroom: 'Classroom instruction', support: 'Support services', admin: 'Administration', operations: 'Operations & maintenance',
  extracurricular: 'Extracurricular', classroomSupplies: 'Classroom supplies & textbooks',
  studentTeacherRatio: 'Students per teacher', studentSupportRatio: 'Students per support staffer', studentAdminRatio: 'Students per administrator',
  medianTeacherSalary: 'Median teacher salary', medianSupportSalary: 'Median support-staff salary', medianAdminSalary: 'Median administrator salary',
}

function useSpendIndex(data) {
  return useMemo(() => {
    const m = new Map()
    for (const r of data.spending ?? []) m.set(`${r.year}|${r.entity}|${r.metric}`, r)
    return (year, entity, metric) => m.get(`${year}|${entity}|${metric}`)
  }, [data])
}

// Format a metric value according to its unit.
function fmtMetric(meta, metric, v, asPct = false) {
  if (v == null) return '–'
  if (asPct) return `${fmt(v)}%`
  const unit = meta.metrics[metric]?.unit
  if (unit === 'usd') return money(v)
  if (unit === 'pct') return `${fmt(v)}%`
  return fmt(v)
}

// refs: [{ value, label, stroke, dash }] reference lines (e.g., state average, Top-20 cutoff).
function PeerBars({ title, rows, refs = [], format, note }) {
  const shown = refs.filter((r) => r.value != null)
  const max = Math.max(...rows.map((r) => r.v ?? 0), ...shown.map((r) => r.value))
  return (
    <div className="rounded-lg p-3" style={{ background: 'var(--surface-2)' }}>
      <div className="text-sm font-semibold">{title}</div>
      <div className="text-xs text-muted tabular mb-1 flex flex-wrap gap-x-3">
        {refs.map((r) => (
          <span key={r.label} className="inline-flex items-center gap-1">
            <svg width="16" height="6" aria-hidden="true"><line x1="0" y1="3" x2="16" y2="3" stroke={r.stroke} strokeWidth="2" strokeDasharray={r.dash} /></svg>
            {r.label} {format(r.value)}
          </span>
        ))}
      </div>
      <ResponsiveContainer width="100%" height={rows.length * 30 + 12}>
        <BarChart data={rows.map((r) => ({ ...r, label: format(r.v) }))} layout="vertical" margin={{ top: 4, right: 0, left: 0, bottom: 4 }} barCategoryGap={4}>
          <XAxis type="number" hide domain={[0, max * 1.04]} />
          <YAxis type="category" dataKey="district" {...axisProps} width={128} axisLine={false} tick={{ fontSize: 11 }} />
          {/* Values in their own right-hand column so they never collide with the reference lines. */}
          <YAxis yAxisId="values" orientation="right" type="category" dataKey="label" {...axisProps} axisLine={false} width={58} tick={{ fontSize: 12 }} />
          <Tooltip formatter={(v) => format(v)} cursor={{ fill: 'var(--band)' }} />
          <Bar dataKey="v" name={title} radius={[0, 4, 4, 0]} isAnimationActive={false}>
            {rows.map((r) => <Cell key={r.code} fill={DISTRICT_COLOR[r.code]} />)}
          </Bar>
          {shown.map((r) => <ReferenceLine key={r.label} x={r.value} stroke={r.stroke} strokeDasharray={r.dash} strokeWidth={1.5} ifOverflow="extendDomain" />)}
        </BarChart>
      </ResponsiveContainer>
      {note && <div className="text-xs text-muted mt-1">{note}</div>}
    </div>
  )
}
const spendRefs = (state, group, top20) => [
  ...(top20 !== undefined ? [{ value: top20, label: 'Top 20 median', stroke: 'var(--text-primary)', dash: '6 3' }] : []),
  { value: state, label: 'State', stroke: top20 !== undefined ? 'var(--neutral-series)' : 'var(--text-primary)', dash: top20 !== undefined ? '2 3' : '5 4' },
  { value: group, label: 'K-12 3,501+', stroke: top20 !== undefined ? 'var(--text-muted)' : 'var(--neutral-series)', dash: top20 !== undefined ? '1 4' : '2 3' },
]
const pctDiff = (a, b) => (a == null || b == null || !b ? null : ((a - b) / b) * 100)

function Spending({ data }) {
  const meta = data.meta.spending
  const get = useSpendIndex(data)
  const districts = orderedDistricts(data.meta)
  const name = (d) => data.meta.districts[d].short
  const years = meta?.years ?? []
  const [year, setYear] = useState(meta?.latestActualYear ?? years[years.length - 1])
  const [mode, setMode] = useState('pct')
  const [trendMetric, setTrendMetric] = useState('classroom')
  if (!meta || !years.length) return <div className="card p-4">Spending data has not been loaded yet.</div>

  const isBudget = (y) => get(y, TARGET, 'budgetaryCost')?.basis === 'budgeted'
  const stageOf = (y) => get(y, TARGET, 'budgetaryCost')?.stage ?? 'budget'
  const yearOptions = [...years].reverse().map((y) => ({ value: y, label: isBudget(y) ? `${y} (${stageOf(y)})` : y }))
  const stateRec = get(year, STATE, 'budgetaryCost')
  const stateNote = !stateRec
    ? `NJDOE hasn't published state or group averages for ${year} yet; they arrive with its Taxpayers' Guide, about a year after the budget. District figures for ${year} come from the User-Friendly Budget summaries, so only the five districts are compared and there are no ranks.`
    : stateRec.basis === 'budgeted' && !isBudget(year)
      ? `District figures for ${year} are audited actuals, but NJDOE's state and group averages for that year are still budget figures. Compare with care.`
      : null
  const asPct = mode === 'pct'
  const val = (y, e, m, pct = asPct) => { const r = get(y, e, m); return (pct ? r?.pct : r?.value) ?? null }
  const rankOf = (y, m) => { const r = get(y, TARGET, m)?.rank; return r && r.includes('|') ? r.split('|') : null }

  // KPI tiles: BRRSD vs. state average for the selected year.
  const tile = (m, pct) => {
    const b = val(year, TARGET, m, pct), s = val(year, STATE, m, pct)
    return { b, s, diff: b != null && s != null ? b - s : null }
  }
  const cost = tile('budgetaryCost', false)
  const kpis = [['classroom', 'Classroom share'], ['support', 'Support services share'], ['admin', 'Administration share']]
    .map(([m, label]) => ({ m, label, ...tile(m, true) }))
  const k12Peers = districts.filter((d) => d !== TARGET && data.meta.districts[d].grades !== '09-12')
  const peerAvg = (y, m, pct) => {
    const vs = k12Peers.map((d) => val(y, d, m, pct)).filter((v) => v != null)
    return vs.length ? vs.reduce((a, b) => a + b, 0) / vs.length : null
  }
  const top20Rec = get(year, TOP20, 'budgetaryCost')
  const top20Info = meta.top20
  const top20Members = top20Rec ? top20Info?.members?.[top20Rec.topYear] ?? [] : []
  const admin = { b: val(year, TARGET, 'admin', false), t: val(year, TOP20, 'admin', false), p: peerAvg(year, 'admin', false), s: val(year, STATE, 'admin', false) }

  // Variance table: BRRSD vs. K-12 peer average, Top 20 median and state average.
  const variance = VARIANCE_CATS.map(([m, pct]) => {
    const info = meta.metrics[m]
    const b = val(year, TARGET, m, pct), p = peerAvg(year, m, pct), t = val(year, TOP20, m, pct), st = val(year, STATE, m, pct)
    const isUsd = info?.unit === 'usd' && !pct
    return {
      metricKey: m, pct, label: `${info?.label ?? m}${pct ? ' (% of budgetary cost)' : ''}`, isUsd,
      brrsd: b, peerAvg: p, top20Median: t, stateAvg: st,
      vsPeers: b != null && p != null ? b - p : null, vsPeersPct: isUsd ? pctDiff(b, p) : null,
      vsTop20: b != null && t != null ? b - t : null, vsTop20Pct: isUsd ? pctDiff(b, t) : null,
      vsState: b != null && st != null ? b - st : null,
    }
  }).filter((r) => r.brrsd != null)
  const vFmt = (r) => (v) => fmtMetric(meta, r.metricKey, v, r.pct)
  const vDiff = (r, abs, rel) => {
    if (abs == null) return '–'
    const sign = abs > 0 ? '+' : abs < 0 ? '−' : '±'
    const body = r.isUsd ? money(Math.abs(abs)) : r.pct || meta.metrics[r.metricKey]?.unit === 'pct' ? `${fmt(Math.abs(abs))} pts` : fmt(Math.abs(abs))
    return <span>{sign}{body}{rel != null && <span className="text-muted"> ({rel > 0 ? '+' : rel < 0 ? '−' : ''}{fmt(Math.abs(rel))}%)</span>}</span>
  }
  const varianceExport = variance.map((r) => ({
    year, indicator: r.label, BRRSD: r.brrsd, 'K-12 peer average': r.peerAvg, 'Top 20 median': r.top20Median, 'State average': r.stateAvg,
    'BRRSD minus peers': r.vsPeers, 'BRRSD vs peers (%)': r.vsPeersPct, 'BRRSD minus Top 20 median': r.vsTop20,
    'BRRSD vs Top 20 median (%)': r.vsTop20Pct, 'BRRSD minus state': r.vsState,
  }))

  const fmtV = (m) => (v) => fmtMetric(meta, m, v, asPct && meta.metrics[m]?.unit === 'usd')
  const barRows = (m, pct) => districts.map((d) => ({ district: name(d), code: d, v: val(year, d, m, pct) }))

  // Trend: one metric over time, districts plus state/group averages.
  const trendPct = asPct && trendMetric !== 'budgetaryCost'
  const trend = years.map((y) => {
    const row = { year: isBudget(y) ? `${y}*` : y }
    for (const d of districts) row[name(d)] = val(y, d, trendMetric, trendPct)
    row['State average'] = val(y, STATE, trendMetric, trendPct)
    row['K-12 3,501+ average'] = val(y, GROUP, trendMetric, trendPct)
    row['Top 20 median'] = val(y, TOP20, trendMetric, trendPct)
    return row
  })
  const trendFmt = (v) => fmtMetric(meta, trendMetric, v, trendPct)

  // Detail table: every indicator for every entity in the selected year.
  const entities = [...districts.map((d) => ({ id: d, label: name(d) })), { id: TOP20, label: 'Top 20 median' }, { id: GROUP, label: 'K-12 3,501+ avg' }, { id: STATE, label: 'State avg' }]
  const detail = TABLE_CATS.map((m) => {
    const info = meta.metrics[m]
    const hasPct = get(year, TARGET, m)?.pct != null
    const row = { metric: info?.label ?? m, metricKey: m, indicator: info?.indicator }
    for (const e of entities) {
      row[e.id] = get(year, e.id, m)?.value ?? null
      if (hasPct) row[`${e.id} %`] = get(year, e.id, m)?.pct ?? null
    }
    const rk = rankOf(year, m)
    row.brrsdRank = rk ? `${rk[0]} of ${rk[1]}` : null
    return row
  })
  const detailCols = [
    { key: 'metric', label: 'Indicator' },
    ...entities.map((e) => ({ key: e.id, label: e.label, render: (v, r) => (
      <span>{fmtMetric(meta, r.metricKey, v)}{r[`${e.id} %`] != null && <span className="text-muted"> · {fmt(r[`${e.id} %`])}%</span>}</span>) })),
    { key: 'brrsdRank', label: 'BRRSD rank', render: (v) => v ?? '–' },
  ]
  const detailExport = detail.map((r) => {
    const o = { year, basis: isBudget(year) ? 'budgeted' : 'actual', indicator: r.indicator, metric: r.metric }
    for (const e of entities) { o[e.label] = r[e.id]; if (`${e.id} %` in r) o[`${e.label} (% of budgetary cost)`] = r[`${e.id} %`] }
    o['BRRSD rank (1 = lowest cost / leanest)'] = r.brrsdRank
    return o
  })
  const rawRows = (data.spending ?? []).filter((r) => r.year === year)

  // Plain-language observations computed from the data (no hand-written numbers).
  const firstYr = years.find((y) => val(y, TARGET, 'classroom', true) != null)
  const stateFirst = years.find((y) => y >= firstYr && val(y, STATE, 'classroom', true) != null)
  const lastActual = meta.latestActualYear
  const obs = []
  const c0 = val(firstYr, TARGET, 'classroom', true), c1 = val(lastActual, TARGET, 'classroom', true)
  const stateLast = [...years].reverse().find((y) => get(y, STATE, 'classroom')?.basis === 'actual')
  const s0 = val(stateFirst, STATE, 'classroom', true), s1 = val(stateLast, STATE, 'classroom', true)
  if (c0 != null && c1 != null) {
    obs.push(`BRRSD's classroom-instruction share went from ${fmt(c0)}% in ${firstYr} to ${fmt(c1)}% in ${lastActual} (${signed(c1 - c0)})`
      + (s0 != null && s1 != null ? `; the state average went from ${fmt(s0)}% in ${stateFirst} to ${fmt(s1)}% in ${stateLast} (${signed(s1 - s0)}).` : '.'))
  }
  const sp0 = val(firstYr, TARGET, 'support', true), sp1 = val(lastActual, TARGET, 'support', true)
  if (sp0 != null && sp1 != null) obs.push(`Support services rose from ${fmt(sp0)}% to ${fmt(sp1)}% of BRRSD's budgetary cost (${signed(sp1 - sp0)}).`)
  const bc = val(stateLast, TARGET, 'budgetaryCost', false), bcs = val(stateLast, STATE, 'budgetaryCost', false)
  const bcr = rankOf(stateLast, 'budgetaryCost')
  if (bc != null && bcs != null) obs.push(`Budgetary cost per pupil in ${stateLast}: ${money(bc)} vs. ${money(bcs)} state average${bcr ? `, ranked ${bcr[0]} of ${bcr[1]} large K-12 districts (1 = lowest)` : ''}.`)
  const newest = years[years.length - 1]
  const prior = years[years.length - 2]
  const nb = val(newest, TARGET, 'budgetaryCost', false), pb = val(prior, TARGET, 'budgetaryCost', false)
  if (isBudget(newest) && nb != null && pb != null) {
    const peersUp = districts.filter((d) => d !== TARGET && val(newest, d, 'budgetaryCost', false) != null)
      .map((d) => `${name(d)} ${money(val(newest, d, 'budgetaryCost', false))}`)
    obs.push(`The ${stageOf(newest)} for ${newest} sets budgetary cost at ${money(nb)} per pupil (${signed(((nb - pb) / pb) * 100, '%')} vs. ${prior}), with ${fmt(val(newest, TARGET, 'classroom', true))}% for classroom instruction. Peers: ${peersUp.join(', ')}.`)
  }

  const trendOptions = ['classroom', 'support', 'admin', 'operations', 'extracurricular', 'classroomSupplies', 'legal', 'budgetaryCost', 'benefitsPctSalaries']
    .map((m) => ({ value: m, label: meta.metrics[m]?.label ?? m }))

  return (
    <div className="space-y-5">
      <div className="card p-4 text-sm text-secondary leading-relaxed">
        <strong className="text-[color:var(--text-primary)]">Where each dollar goes, compared fairly.</strong> These figures come from
        NJDOE's <a className="underline" href={meta.source}>Taxpayers' Guide to Education Spending</a>, extended with the
        newest years from the <a className="underline" href={meta.budgetSource}>User-Friendly Budget summaries</a>. "Budgetary cost per pupil"
        puts each district's employee benefits back into the category where the staff work, and leaves out transportation,
        out-of-district tuition, debt, construction and state-paid pensions, so districts of different shapes can be compared.
        The state and "K-12 3,501+" lines are NJDOE's own averages.
      </div>
      {obs.length > 0 && (
        <ul className="card p-4 text-sm leading-relaxed list-disc pl-8 space-y-1">
          {obs.map((o) => <li key={o}>{o}</li>)}
        </ul>
      )}
      <div className="flex flex-wrap gap-4 items-center">
        <Control label="Year" value={year} onChange={setYear} options={yearOptions} />
        <Control label="Show" value={mode} onChange={setMode}
          options={[{ value: 'pct', label: '% of budgetary cost' }, { value: 'usd', label: '$ per pupil' }]} />
      </div>
      {isBudget(year) && (
        <div className="card p-3 text-sm text-secondary" role="note">
          <span aria-hidden="true" style={{ color: 'var(--warning)' }}>▲</span> {year} figures are the <strong>{stageOf(year)}</strong>,
          not audited actuals. They're replaced with actuals once NJDOE publishes them.
        </div>
      )}
      {stateNote && (
        <div className="card p-3 text-sm text-secondary" role="note">
          <span aria-hidden="true" style={{ color: 'var(--warning)' }}>▲</span> {stateNote}
        </div>
      )}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <Stat label={`Budgetary cost / pupil · ${year}`} value={money(cost.b)} neutral
          note={[val(year, TOP20, 'budgetaryCost', false) != null && `Top 20 median ${money(val(year, TOP20, 'budgetaryCost', false))}`,
            cost.s != null && `State avg ${money(cost.s)}`].filter(Boolean).join(' · ') || undefined} />
        <Stat label={`Administration $ / pupil · ${year}`} value={money(admin.b)} neutral
          note={[admin.t != null && `Top 20 median ${money(admin.t)}`, admin.p != null && `Peers ${money(admin.p)}`,
            admin.s != null && `State ${money(admin.s)}`].filter(Boolean).join(' · ') || undefined} />
        {kpis.map((k) => (
          <Stat key={k.m} label={`${k.label} · ${year}`} value={fmtPct(k.b)} delta={k.diff} deltaLabel="vs state avg" neutral
            note={[val(year, TOP20, k.m, true) != null && `Top 20 median ${fmtPct(val(year, TOP20, k.m, true))}`, k.s != null && `State avg ${fmtPct(k.s)}`].filter(Boolean).join(' · ') || undefined} />
        ))}
      </div>

      <Card title={`Spending variance vs. peers and the Top 20 · ${year}`}
        subtitle="Positive = BRRSD spends more (or, for staff ratios, has more students per staff member)"
        name={`spending_variance_${year}`} rows={varianceExport}
        footnote={`Peer average = ${k12Peers.map(name).join(', ')} (K-12 districts; Watchung Hills, a 9-12 regional, is left out because its cost mix isn't comparable). ${top20Info?.definition ?? ''}${top20Rec ? ` This year uses the ${top20Rec.topYear} Top 20 (${top20Rec.n} with spending data)${top20Rec.basis === 'budgeted' ? '; their figures for this year are still budgets, not audited actuals' : ''}.` : ' NJDOE has not yet published every district\'s figures for this year, so there is no Top 20 median.'} A higher or lower figure isn't good or bad by itself: it shows where BRRSD's spending differs.`}>
        <div className="overflow-x-auto">
          <table className="data-table tabular">
            <thead><tr>
              <th scope="col" style={{ textAlign: 'left' }}>Indicator</th><th scope="col">BRRSD</th>
              <th scope="col">Peer avg</th><th scope="col">vs peers</th>
              <th scope="col">Top 20 median</th><th scope="col">vs Top 20</th>
              <th scope="col">State avg</th><th scope="col">vs state</th>
            </tr></thead>
            <tbody>
              {variance.map((r) => (
                <tr key={r.label} style={r.metricKey === 'admin' ? { background: 'var(--surface-2)', fontWeight: 600 } : undefined}>
                  <td style={{ textAlign: 'left' }}>{r.label}</td>
                  <td>{vFmt(r)(r.brrsd)}</td>
                  <td>{vFmt(r)(r.peerAvg)}</td><td>{vDiff(r, r.vsPeers, r.vsPeersPct)}</td>
                  <td>{vFmt(r)(r.top20Median)}</td><td>{vDiff(r, r.vsTop20, r.vsTop20Pct)}</td>
                  <td>{vFmt(r)(r.stateAvg)}</td><td>{vDiff(r, r.vsState, null)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {top20Members.length > 0 && (
          <details className="mt-3 text-sm">
            <summary className="cursor-pointer text-secondary">Which districts are in the Top 20 ({top20Rec.topYear} ELA + Math proficiency)?</summary>
            <ol className="mt-2 grid sm:grid-cols-2 lg:grid-cols-4 gap-x-6 gap-y-1 list-decimal pl-6 text-secondary">
              {top20Members.map((m) => <li key={m.districtCode}>{m.name} <span className="text-muted tabular">{fmtPct(m.value)}</span></li>)}
            </ol>
          </details>
        )}
      </Card>

      <Card title={`Spending categories vs. neighbors · ${year}`}
        subtitle={asPct ? 'Each category as a share of budgetary cost per pupil' : 'Dollars per pupil (budgetary cost basis)'}
        name={`spending_categories_${year}`} rows={SPEND_CATS.flatMap((m) => barRows(m, asPct && meta.metrics[m]?.unit === 'usd').map((r) => ({ year, metric: meta.metrics[m]?.label, district: r.district, value: r.v, stateAvg: val(year, STATE, m), groupAvg: val(year, GROUP, m) })))}
        footnote="Top 20 median = the median of the 20 NJ K-12 districts with the highest ELA + Math proficiency. State = state average (all operating types). K-12 3,501+ = average of NJ K-12 districts with 3,501+ students (BRRSD's NJDOE comparison group). Watchung Hills is a 9-12 regional district in a different NJDOE group, so its mix naturally differs.">
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {SPEND_CATS.map((m) => {
            const pct = asPct && meta.metrics[m]?.unit === 'usd'
            const f = (v) => fmtMetric(meta, m, v, pct)
            const rk = rankOf(year, m)
            return <PeerBars key={m} title={SHORT[m]} rows={barRows(m, pct)} refs={spendRefs(val(year, STATE, m, pct), val(year, GROUP, m, pct), val(year, TOP20, m, pct))}
              format={f} note={rk ? `BRRSD ranks ${rk[0]} of ${rk[1]} on $/pupil (1 = lowest)` : null} />
          })}
        </div>
      </Card>

      <Card title={`${meta.metrics[trendMetric]?.label ?? trendMetric} over time`}
        subtitle={`${trendPct ? '% of budgetary cost' : meta.metrics[trendMetric]?.unit === 'usd' ? 'Dollars per pupil' : 'Percent'}. * = budgeted year.`}
        name={`spending_trend_${trendMetric}`} rows={trend}
        table={<DataTable rows={trend} columns={[{ key: 'year', label: 'Year' },
          ...[...districts.map(name), 'Top 20 median', 'K-12 3,501+ average', 'State average'].map((k) => ({ key: k, label: k, render: trendFmt }))]} />}
        footnote="Figures for each year come from the newest NJDOE edition that reports that year as an actual. Earlier editions sometimes restate a year slightly.">
        <div className="mb-3"><Control label="Category" value={trendMetric} onChange={setTrendMetric} options={trendOptions} /></div>
        <ResponsiveContainer width="100%" height={320}>
          <LineChart data={trend} margin={{ top: 16, right: 16, left: 8, bottom: 0 }}>
            <CartesianGrid {...gridProps} />
            <XAxis dataKey="year" {...axisProps} />
            <YAxis {...axisProps} width={56} domain={['auto', 'auto']} tickFormatter={trendFmt} />
            <Tooltip formatter={trendFmt} />
            <Legend />
            {districts.map((d) => (
              <Line key={d} dataKey={name(d)} stroke={DISTRICT_COLOR[d]} strokeWidth={d === TARGET ? 3 : 2} dot={{ r: d === TARGET ? 5 : 3 }}
                connectNulls={false} isAnimationActive={false} />
            ))}
            <Line dataKey="Top 20 median" stroke="var(--text-primary)" strokeDasharray="6 3" strokeWidth={2} dot={false} isAnimationActive={false} />
            <Line dataKey="State average" stroke="var(--neutral-series)" strokeDasharray="2 3" strokeWidth={1.5} dot={false} isAnimationActive={false} />
            <Line dataKey="K-12 3,501+ average" stroke="var(--text-muted)" strokeDasharray="1 4" strokeWidth={1.5} dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </Card>

      <Card title={`Staffing levels & pay vs. neighbors · ${year}`} subtitle="Students per staff member (higher = fewer staff per student) and median salaries"
        name={`staffing_peers_${year}`} rows={STAFF_CATS.flatMap((m) => barRows(m, false).map((r) => ({ year, metric: meta.metrics[m]?.label, district: r.district, value: r.v, stateAvg: val(year, STATE, m, false), groupAvg: val(year, GROUP, m, false) })))}
        footnote="NJDOE staff ratios use full-time-equivalent staff as of October. Support staff include guidance counselors, librarians, nurses, child-study teams and similar roles. A lower students-per-staff number means more staff per student.">
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {STAFF_CATS.map((m) => {
            const rk = rankOf(year, m)
            return <PeerBars key={m} title={SHORT[m]} rows={barRows(m, false)} refs={spendRefs(val(year, STATE, m, false), val(year, GROUP, m, false), val(year, TOP20, m, false))}
              format={(v) => fmtMetric(meta, m, v)} note={rk ? `BRRSD ranks ${rk[0]} of ${rk[1]}` : null} />
          })}
        </div>
      </Card>

      <Card title={`All NJDOE spending indicators · ${year}`} subtitle="$ per pupil · share of budgetary cost (where NJDOE reports one)"
        name={`spending_indicators_${year}`} rows={detailExport}
        footnote={`Ranks are among ${meta.groupLabels?.G ?? 'large K-12 districts'}; NJDOE ranks 1 = lowest cost, or for staff ratios, 1 = most students per staff member. Salary-and-benefit lines are subsets of the category above them. Total spending per pupil counts every fund (including transportation, tuition, debt and state-paid pensions) and isn't comparable to budgetary cost.`}>
        <div className="overflow-x-auto">
          <DataTable rows={detail} columns={detailCols} />
        </div>
        <div className="mt-3 flex flex-wrap gap-2 items-center text-xs text-muted">
          <span>Raw records for {year}:</span><DownloadButtons name={`spending_raw_${year}`} rows={rawRows} />
        </div>
      </Card>
    </div>
  )
}

/* ------------------------------------------------------------------------- */
/* App shell                                                                  */
/* ------------------------------------------------------------------------- */
export default function App() {
  const [data, setData] = useState(null)
  const [fiscal, setFiscal] = useState(null)
  const [error, setError] = useState(null)
  const [tab, setTab] = useState(() => (typeof location !== 'undefined' && location.hash.slice(1)) || 'overview')

  useEffect(() => {
    fetch('/data/brrsdData.json', { cache: 'no-cache' })
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() })
      .then(setData)
      .catch((e) => setError(e.message))
    fetch('/data/fiscal_overrides.json', { cache: 'no-cache' }).then((r) => (r.ok ? r.json() : null)).then(setFiscal).catch(() => {})
  }, [])
  useEffect(() => {
    const onHash = () => setTab(location.hash.slice(1) || 'overview')
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  const choose = (id) => { history.replaceState(null, '', `#${id}`); setTab(id) }

  const verified = data?.meta?.lastVerifiedAgainstNJDOE
    ? new Date(data.meta.lastVerifiedAgainstNJDOE).toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })
    : null

  return (
    <div className="min-h-screen">
      <header className="border-b hero" style={{ borderColor: 'var(--border)' }}>
        <div className="max-w-6xl mx-auto px-4 pt-6 pb-3">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-xs font-semibold uppercase tracking-wider" style={{ color: 'var(--accent-ink)' }}>Independent civic dashboard · NJDOE 35-0555</p>
              <h1 className="text-2xl sm:text-3xl font-semibold mt-1">Bridgewater-Raritan Schools, by the Numbers</h1>
              <p className="text-sm text-secondary mt-1 max-w-2xl">
                An easy way for Bridgewater-Raritan residents and parents to see how our schools rank against neighboring
                districts, the state and New Jersey's Top 20 districts, on state test results and budgets. Built from New Jersey
                Department of Education data and updated automatically each month.
              </p>
            </div>
            {verified && (
              <div className="rounded-full px-3 py-1.5 text-xs font-medium flex items-center gap-1.5"
                style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
                title={`Latest NJDOE report year in this dataset: ${data.meta.latestYear}`}>
                <span aria-hidden="true" style={{ color: 'var(--good)' }}>●</span>
                Last verified against NJDOE archives: {verified} · data through {data.meta.latestYear}
              </div>
            )}
          </div>
          <nav className="flex gap-1 mt-5 overflow-x-auto pb-1" role="tablist" aria-label="Dashboard views">
            {TABS.map((t) => (
              <button key={t.id} role="tab" aria-selected={tab === t.id} className="tab" onClick={() => choose(t.id)}>{t.label}</button>
            ))}
          </nav>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-4 py-6">
        {error && <div className="card p-4" role="alert">Could not load data ({error}). Try refreshing.</div>}
        {!data && !error && <div className="text-secondary">Loading NJDOE data…</div>}
        {data && (
          <>
            {tab === 'overview' && <Overview data={data} />}
            {tab === 'achievement' && <Achievement data={data} />}
            {tab === 'recovery' && <Recovery data={data} />}
            {tab === 'growth' && <Growth data={data} />}
            {tab === 'schools' && <Schools data={data} />}
            {tab === 'budget' && <Budget data={data} fiscal={fiscal} />}
            {tab === 'spending' && <Spending data={data} />}
          </>
        )}
      </main>

      <footer className="max-w-6xl mx-auto px-4 pb-10 text-xs text-muted leading-relaxed space-y-2">
        {data && (
          <p>
            Full datasets:{' '}
            {['proficiency', 'growth', 'absenteeism', 'staffing', 'algebra1', 'highschool', 'spending', 'budget', 'benchmarks'].map((f, i) => (
              <span key={f}>{i > 0 && ' · '}<a className="underline" href={`/data/exports/${f}.csv`} download>{f}.csv</a></span>
            ))}{' · '}<a className="underline" href="/data/brrsdData.json" download>brrsdData.json</a>
          </p>
        )}
        <p>
          Sources: <a className="underline" href="https://www.nj.gov/education/spr/download/">NJDOE School Performance Reports</a> and{' '}
          <a className="underline" href="https://www.nj.gov/education/guide/">NJDOE Taxpayers' Guide to Education Spending</a>.
          Suppressed NJDOE values (small groups) are left blank, never estimated. Rows marked “derived” in the downloads were
          computed from NJDOE school-level data.
        </p>
        <p className="font-medium text-secondary">
          An independent project, not affiliated with or endorsed by the Bridgewater-Raritan Regional School District.
          Every figure comes from New Jersey Department of Education public data.
        </p>
      </footer>
    </div>
  )
}

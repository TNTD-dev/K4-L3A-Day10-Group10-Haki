"""Static HTML observability dashboard (Bonus B1). Khong them dependency: doc JSON artifacts -> 1 file HTML."""
from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from core.config import Settings
from core.utils import now_utc, read_json, write_text
from observability.quality import freshness_report_path, quality_report_path
from observability.reporting import METRIC_LABELS, _detected, is_recovered

STATES = [("baseline", "Baseline"), ("corrupted", "Corrupted"), ("repaired", "Repaired")]


def _load(path: Path) -> dict[str, Any] | None:
    return read_json(path) if path.exists() else None


def collect_artifacts(settings: Settings) -> dict[str, Any]:
    p = settings.paths
    return {
        "metrics": {
            "baseline": _load(p.baseline_metrics),
            "corrupted": _load(p.corrupted_metrics),
            "repaired": _load(p.repaired_metrics),
        },
        "quality": {s: _load(quality_report_path(settings, s)) for s, _ in STATES},
        "freshness": {s: _load(freshness_report_path(settings, s)) for s, _ in STATES},
        "corruption_log": _load(p.corruption_log),
    }


def _pill(ok: bool | None, yes: str = "PASS", no: str = "FAIL") -> str:
    if ok is None:
        return '<span class="pill muted">N/A</span>'
    return f'<span class="pill {"ok" if ok else "bad"}">{yes if ok else no}</span>'


def _flow_strip(a: dict) -> str:
    q, f = a["quality"], a["freshness"]
    steps = []
    for key, label in STATES:
        gate = q[key]["success"] if q[key] else None
        fresh = f[key]["is_fresh"] if f[key] else None
        steps.append(
            f'<div class="step {key}"><div class="step-title">{label}</div>'
            f'<div>GX gate {_pill(gate)}</div><div>Freshness {_pill(fresh, "FRESH", "STALE")}</div></div>'
        )
    return '<div class="flow">' + '<div class="arrow">→</div>'.join(steps) + "</div>"


def _kpis(a: dict) -> str:
    m = a["metrics"]
    base = m["baseline"] or {}
    cards = []
    for key, label in METRIC_LABELS:
        cells = []
        for state, state_label in STATES:
            v = (m[state] or {}).get(key)
            delta = ""
            if state != "baseline" and isinstance(v, (int, float)) and isinstance(base.get(key), (int, float)):
                d = v - base[key]
                delta = f'<span class="delta {"down" if d < -1e-9 else "flat"}">{d:+.3f}</span>'
            val = f"{v:.3f}" if isinstance(v, (int, float)) else "—"
            cells.append(f'<div class="kpi-cell {state}"><small>{state_label}</small><b>{val}</b>{delta}</div>')
        cards.append(f'<div class="card"><h3>{escape(label)}</h3><div class="kpi-row">{"".join(cells)}</div></div>')
    return '<div class="grid">' + "".join(cards) + "</div>"


def _bar_chart(a: dict) -> str:
    m = a["metrics"]
    # Chi ve metric thang 0-1; judge score (1-5) quy ve 0-1.
    w, h, pad = 640, 220, 30
    group_w = (w - pad) / len(METRIC_LABELS)
    bar_w = group_w / 4
    bars, labels = [], []
    for gi, (key, label) in enumerate(METRIC_LABELS):
        for si, (state, _) in enumerate(STATES):
            v = (m[state] or {}).get(key)
            if not isinstance(v, (int, float)):
                continue
            norm = v / 5 if key == "mean_judge_score" else v
            bh = max(1.0, norm * (h - 40))
            x = pad + gi * group_w + si * bar_w + bar_w / 2
            bars.append(f'<rect class="bar {state}" x="{x:.1f}" y="{h - 20 - bh:.1f}" width="{bar_w - 4:.1f}" height="{bh:.1f}" rx="3"><title>{label} · {state}: {v:.3f}</title></rect>')
        labels.append(f'<text x="{pad + gi * group_w + group_w / 2:.1f}" y="{h - 4}" text-anchor="middle">{escape(label.split(" (")[0])}</text>')
    axis = f'<line x1="{pad}" y1="{h - 20}" x2="{w}" y2="{h - 20}" class="axis"/>'
    legend = "".join(f'<span class="legend {s}">{l}</span>' for s, l in STATES)
    return f'<div class="card wide"><h3>RAG metrics — 3 trạng thái</h3><div class="legend-row">{legend}</div><svg viewBox="0 0 {w} {h}" role="img" aria-label="Grouped bar chart">{axis}{"".join(bars)}{"".join(labels)}</svg><p class="note">Judge score quy về thang 0–1 (÷5).</p></div>'


def _gx_matrix(a: dict) -> str:
    q = a["quality"]
    names: list[str] = []
    for s, _ in STATES:
        for r in (q[s] or {}).get("results", []):
            name = r["expectation"] + (f" ({r['column']})" if r.get("column") else "")
            if name not in names:
                names.append(name)
    rows = []
    for name in names:
        cells = []
        for s, _ in STATES:
            match = next((r for r in (q[s] or {}).get("results", []) if r["expectation"] + (f" ({r['column']})" if r.get("column") else "") == name), None)
            if match is None:
                cells.append('<td class="muted">—</td>')
            else:
                extra = f' <small>{match["unexpected_count"]} lỗi</small>' if match["unexpected_count"] else ""
                cells.append(f'<td>{_pill(match["success"], "✓", "✗")}{extra}</td>')
        rows.append(f"<tr><th>{escape(name)}</th>{''.join(cells)}</tr>")
    head = "".join(f"<th>{l}</th>" for _, l in STATES)
    return f'<div class="card wide"><h3>Great Expectations 1.x — ma trận kiểm định</h3><table><thead><tr><th>Expectation</th>{head}</tr></thead><tbody>{"".join(rows) or "<tr><td>Chưa có báo cáo</td></tr>"}</tbody></table></div>'


def _freshness(a: dict) -> str:
    f = a["freshness"]
    blocks = []
    for s, label in STATES:
        rep = f[s]
        if not rep:
            blocks.append(f'<div class="fresh {s}"><h4>{label}</h4><p class="muted">Chưa có</p></div>')
            continue
        ratio, limit, threshold = rep["stale_ratio"], rep["max_stale_ratio"], rep["threshold_days"]
        ages = rep.get("age_days", [])
        bins = [0] * 8  # 60 ngay / bin, bin cuoi = 420+
        for age in ages:
            bins[min(7, max(0, age) // 60)] += 1
        peak = max(bins) or 1
        hist = "".join(
            f'<rect x="{i * 30 + 2}" y="{60 - c / peak * 56:.1f}" width="26" height="{c / peak * 56:.1f}" class="hbar {"stale" if i * 60 >= threshold else ""}"><title>{i * 60}-{i * 60 + 59} ngày: {c}</title></rect>'
            for i, c in enumerate(bins)
        )
        marker = threshold / 60 * 30
        blocks.append(
            f'<div class="fresh {s}"><h4>{label} {_pill(rep["is_fresh"], "FRESH", "STALE")}</h4>'
            f'<div class="gauge"><div class="fill {"bad" if ratio > limit else "ok"}" style="width:{min(ratio, 1) * 100:.1f}%"></div><div class="limit" style="left:{limit * 100:.0f}%"></div></div>'
            f'<small>stale {rep["stale_rows"]}/{rep["total_rows"]} = {ratio:.0%} (SLA ≤ {limit:.0%}) · bài mới nhất {rep.get("latest_age_days", "—")} ngày</small>'
            f'<svg viewBox="0 0 240 64" class="hist" role="img" aria-label="Age histogram">{hist}<line x1="{marker}" y1="0" x2="{marker}" y2="64" class="threshold"/></svg>'
            f'<small class="muted">Phân bố age_days (bin 60 ngày, vạch = {threshold} ngày)</small></div>'
        )
    return f'<div class="card wide"><h3>Freshness SLA</h3><div class="fresh-row">{"".join(blocks)}</div></div>'


def _corruptions(a: dict) -> str:
    log = a["corruption_log"]
    if not log:
        return '<div class="card wide"><h3>Corruption log</h3><p class="muted">Chưa chạy corruption flow.</p></div>'
    q, f = a["quality"]["corrupted"] or {}, a["freshness"]["corrupted"] or {}
    rows = "".join(
        f'<tr><td><code>{escape(c["name"])}</code></td><td>{len(c["affected_paper_ids"])}</td><td>{escape(c["expected_detector"])}</td>'
        f'<td>{_pill(_detected(c, q, f), "DETECTED", "SILENT")}</td></tr>'
        for c in log["scenarios"]
    )
    return f'<div class="card wide"><h3>Corruption → Detection ({log["input_rows"]} → {log["output_rows"]} rows)</h3><table><thead><tr><th>Lỗi</th><th>Dòng</th><th>Detector kỳ vọng</th><th>Kết quả</th></tr></thead><tbody>{rows}</tbody></table></div>'


CSS = """
:root{--bg:#f7f8fa;--card:#fff;--fg:#1c2330;--muted:#6b7280;--line:#e3e6eb;--base:#3b6fd8;--corr:#d9534f;--rep:#2f9e6b;--ok:#2f9e6b;--bad:#d9534f}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#11151c;--card:#1a2029;--fg:#e6e9ee;--muted:#9aa3b2;--line:#2c3440;--base:#6b95ea;--corr:#ef7b77;--rep:#4cc38a;--ok:#4cc38a;--bad:#ef7b77}}
:root[data-theme="dark"]{--bg:#11151c;--card:#1a2029;--fg:#e6e9ee;--muted:#9aa3b2;--line:#2c3440;--base:#6b95ea;--corr:#ef7b77;--rep:#4cc38a;--ok:#4cc38a;--bad:#ef7b77}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px}h1{margin:0 0 4px;font-size:22px}h3{margin:0 0 12px;font-size:15px}h4{margin:0 0 8px;font-size:14px}
.sub{color:var(--muted);margin-bottom:20px}.banner{padding:12px 16px;border-radius:10px;margin-bottom:16px;font-weight:600}
.banner.ok{background:color-mix(in srgb,var(--ok) 15%,transparent)}.banner.bad{background:color-mix(in srgb,var(--bad) 15%,transparent)}.banner.fake{background:color-mix(in srgb,#e0a800 20%,transparent)}
.flow{display:flex;gap:8px;align-items:stretch;margin-bottom:16px;flex-wrap:wrap}.arrow{align-self:center;color:var(--muted);font-size:20px}
.step{flex:1;min-width:180px;background:var(--card);border:1px solid var(--line);border-top:4px solid;border-radius:10px;padding:12px}
.step.baseline{border-top-color:var(--base)}.step.corrupted{border-top-color:var(--corr)}.step.repaired{border-top-color:var(--rep)}.step-title{font-weight:700;margin-bottom:6px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin-bottom:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}.card.wide{margin-bottom:12px;overflow-x:auto}
.kpi-row{display:flex;gap:8px}.kpi-cell{flex:1;border-left:3px solid;padding-left:8px}.kpi-cell b{display:block;font-size:20px}
.kpi-cell.baseline{border-color:var(--base)}.kpi-cell.corrupted{border-color:var(--corr)}.kpi-cell.repaired{border-color:var(--rep)}
.delta{font-size:12px}.delta.down{color:var(--bad)}.delta.flat{color:var(--muted)}small{color:var(--muted)}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600}.pill.ok{background:color-mix(in srgb,var(--ok) 18%,transparent);color:var(--ok)}
.pill.bad{background:color-mix(in srgb,var(--bad) 18%,transparent);color:var(--bad)}.pill.muted,.muted{color:var(--muted)}
svg{width:100%;height:auto}svg text{fill:var(--muted);font-size:11px}.axis{stroke:var(--line)}
.bar.baseline{fill:var(--base)}.bar.corrupted{fill:var(--corr)}.bar.repaired{fill:var(--rep)}
.legend-row{display:flex;gap:14px;margin-bottom:6px}.legend::before{content:"";display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px}
.legend.baseline::before{background:var(--base)}.legend.corrupted::before{background:var(--corr)}.legend.repaired::before{background:var(--rep)}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}th{font-weight:600}
.fresh-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}
.gauge{position:relative;height:10px;background:var(--line);border-radius:5px;margin:6px 0}.gauge .fill{height:100%;border-radius:5px}.fill.ok{background:var(--ok)}.fill.bad{background:var(--bad)}
.gauge .limit{position:absolute;top:-3px;width:2px;height:16px;background:var(--fg)}.hist{margin-top:8px}.hbar{fill:var(--base)}.hbar.stale{fill:var(--bad)}.threshold{stroke:var(--fg);stroke-dasharray:3 2}
.note{color:var(--muted);font-size:12px;margin:4px 0 0}code{font-size:12px}
"""


def render_dashboard(artifacts: dict[str, Any], fake: bool = False) -> str:
    m = artifacts["metrics"]
    q = artifacts["quality"]
    if m["baseline"] and m["repaired"]:
        ok = is_recovered(m["baseline"], m["repaired"])
        banner = f'<div class="banner {"ok" if ok else "bad"}">{"✅ Hệ thống đã phục hồi: Repaired khớp Baseline" if ok else "⚠️ Repaired chưa khớp Baseline"}' \
                 f'{" · Quality Gate đã chặn dữ liệu corrupted" if q["corrupted"] and not q["corrupted"]["success"] else ""}</div>'
    else:
        banner = '<div class="banner bad">Chưa đủ artifacts — chạy run_phase1.py và run_corruption_flow.py</div>'
    if fake:
        banner = '<div class="banner fake">⚠️ FAKE DATA DEMO — metrics RAG là giá trị mô phỏng, không dùng để nộp bài</div>' + banner
    return f"""<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Data Observability Dashboard</title><style>{CSS}</style></head><body><main>
<h1>Data Observability — RAG Pipeline</h1><div class="sub">Crossref papers · GX 1.x Quality Gate · Freshness SLA · Generated {now_utc():%Y-%m-%d %H:%M} UTC</div>
{banner}{_flow_strip(artifacts)}{_kpis(artifacts)}{_bar_chart(artifacts)}{_gx_matrix(artifacts)}{_freshness(artifacts)}{_corruptions(artifacts)}
</main></body></html>"""


def build_dashboard(settings: Settings, fake: bool = False) -> Path:
    out = settings.paths.comparison_report.parent / "dashboard.html"
    write_text(out, render_dashboard(collect_artifacts(settings), fake=fake))
    return out

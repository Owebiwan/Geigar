"""Static HTML report with inline SVG charts (BT-5). No JavaScript, no external assets.

Series are told apart by brightness and texture, never hue, to match the dashboard:
optimizer = solid, naive = hatched, persistence = outline.
"""

from __future__ import annotations

from html import escape

import pandas as pd
from jinja2 import Template

_W, _H, _L, _R, _T, _B = 920, 300, 64, 16, 16, 34


def _fmt(v: float | None, digits: int = 0) -> str:
    return "n/a" if v is None else f"{v:,.{digits}f}"


def uplift_text(pct: float | None) -> str:
    return "n/a (naive rule loses money)" if pct is None else f"{pct:+.1f}%"


def _axis(lo: float, hi: float, ticks: int = 5) -> list[float]:
    step = (hi - lo) / (ticks - 1) if hi > lo else 1.0
    return [lo + i * step for i in range(ticks)]


def _frame(lo: float, hi: float, unit: str) -> tuple[str, float, float]:
    span = (hi - lo) or 1.0
    lo, hi = min(lo, 0.0) - 0.04 * span * (lo < 0), hi + 0.06 * span
    parts = []
    for v in _axis(lo, hi):
        y = _T + (_H - _T - _B) * (1 - (v - lo) / (hi - lo))
        parts.append(f'<line x1="{_L}" x2="{_W - _R}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>')
        parts.append(f'<text x="{_L - 8}" y="{y + 3:.1f}" class="tick" text-anchor="end">'
                     f"{_fmt(v)}</text>")
    parts.append(f'<text x="{_L}" y="{_T - 4}" class="tick">{escape(unit)}</text>')
    return "".join(parts), lo, hi


def _svg(label: str, parts: list[str]) -> str:
    body = "".join(parts)
    return f'<svg viewBox="0 0 {_W} {_H}" role="img" aria-label="{label}">{body}</svg>'


def monthly_bars(monthly: pd.DataFrame) -> str:
    wide = monthly.pivot(index="month", columns="strategy", values="total_eur")
    series = [("optimizer", "solid"), ("naive", "hatch"), ("persistence", "outline")]
    frame, lo, hi = _frame(float(wide[[s for s, _ in series]].min().min()),
                           float(wide[[s for s, _ in series]].max().max()), "EUR / MONTH")
    scale = (_H - _T - _B) / (hi - lo)
    zero = _T + (hi - 0) * scale
    slot = (_W - _L - _R) / len(wide)
    bar = min(18.0, (slot - 14) / 3)
    parts = [frame]
    for i, (month, row) in enumerate(wide.iterrows()):
        x0 = _L + i * slot + (slot - 3 * bar - 4) / 2
        for j, (name, cls) in enumerate(series):
            v = float(row[name])
            h = abs(v) * scale
            y = zero - h if v >= 0 else zero
            parts.append(f'<rect x="{x0 + j * (bar + 2):.1f}" y="{y:.1f}" width="{bar:.1f}" '
                         f'height="{h:.1f}" class="{cls}"><title>{month} {name}: '
                         f"{_fmt(v)} EUR</title></rect>")
        parts.append(f'<text x="{_L + (i + .5) * slot:.1f}" y="{_H - 12}" class="tick" '
                     f'text-anchor="middle">{str(month)[5:]}</text>')
    parts.append(f'<line x1="{_L}" x2="{_W - _R}" y1="{zero:.1f}" y2="{zero:.1f}" class="axis"/>')
    return _svg("Monthly revenue", parts)


def cumulative_lines(daily: pd.DataFrame) -> str:
    wide = daily.pivot(index="date", columns="strategy", values="revenue_eur").cumsum()
    series = [("optimizer", "l-solid"), ("naive", "l-dash"), ("persistence", "l-dot")]
    frame, lo, hi = _frame(float(wide[[s for s, _ in series]].min().min()),
                           float(wide[[s for s, _ in series]].max().max()), "EUR CUMULATIVE")
    n = len(wide)
    parts = [frame]
    for name, cls in series:
        pts = " ".join(
            f"{_L + i / max(n - 1, 1) * (_W - _L - _R):.1f},"
            f"{_T + (_H - _T - _B) * (1 - (float(v) - lo) / (hi - lo)):.1f}"
            for i, v in enumerate(wide[name])
        )
        parts.append(f'<polyline points="{pts}" class="{cls}"/>')
        y_end = _T + (_H - _T - _B) * (1 - (float(wide[name].iloc[-1]) - lo) / (hi - lo))
        parts.append(f'<text x="{_W - _R - 4}" y="{y_end - 6:.1f}" class="tick lbl" '
                     f'text-anchor="end">{name.upper()} {_fmt(float(wide[name].iloc[-1]))}</text>')
    months = pd.to_datetime(wide.index).to_series().dt.strftime("%m")
    for i in [k for k in range(n) if k == 0 or months.iloc[k] != months.iloc[k - 1]]:
        x = _L + i / max(n - 1, 1) * (_W - _L - _R)
        parts.append(f'<text x="{x:.1f}" y="{_H - 12}" class="tick">{months.iloc[i]}</text>')
    return _svg("Cumulative revenue", parts)


_SVG_STYLE = """<style>.grid{stroke:rgba(255,255,255,.08)}.axis{stroke:rgba(255,255,255,.35)}
.tick{fill:#8a8a8a;font:10px ui-monospace,Menlo,monospace}.solid{fill:rgba(255,255,255,.85)}
.outline{fill:none;stroke:rgba(255,255,255,.85)}.hatch{fill:url(#hatch);
stroke:rgba(255,255,255,.35)}.t{fill:#f2f2f2;font:11px ui-monospace,Menlo,monospace}</style>
<defs><pattern id="hatch" width="5" height="5" patternUnits="userSpaceOnUse"
patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="5" stroke="rgba(255,255,255,.85)"
stroke-width="1.5"/></pattern></defs>"""


def standalone_monthly_svg(monthly: pd.DataFrame, title: str) -> str:
    """Self-contained version of the monthly chart for the README."""
    inner = monthly_bars(monthly).split(">", 1)[1].rsplit("</svg>", 1)[0]
    legend = (
        f'<rect x="{_L}" y="{_H + 22}" width="18" height="9" class="solid"/>'
        f'<text x="{_L + 24}" y="{_H + 30}" class="t">OPTIMIZER</text>'
        f'<rect x="{_L + 110}" y="{_H + 22}" width="18" height="9" class="hatch"/>'
        f'<text x="{_L + 134}" y="{_H + 30}" class="t">NAIVE RULE</text>'
        f'<rect x="{_L + 230}" y="{_H + 22}" width="18" height="9" class="outline"/>'
        f'<text x="{_L + 254}" y="{_H + 30}" class="t">PERSISTENCE (NO FORESIGHT)</text>'
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 -34 {_W} {_H + 80}" role="img" '
        f'aria-label="{escape(title)}">{_SVG_STYLE}'
        f'<rect x="0" y="-34" width="{_W}" height="{_H + 80}" fill="#000"/>'
        f'<text x="{_L}" y="-12" class="t">{escape(title)}</text>{inner}{legend}</svg>'
    )


_TEMPLATE = Template("""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Battery dispatch backtest {{ year }}</title>
<style>
:root{--bg:#000;--panel:#0a0a0a;--line:rgba(255,255,255,.85);--dim:rgba(255,255,255,.35);
--grid:rgba(255,255,255,.08);--text:#f2f2f2;--text-dim:#8a8a8a}
body{background:var(--bg);color:var(--text);font:12px/1.5 "JetBrains Mono",ui-monospace,Menlo,
monospace;margin:0;padding:32px;font-variant-numeric:tabular-nums}
main{max-width:960px;margin:auto}
h1,h2{font-weight:500;letter-spacing:.12em;text-transform:uppercase}
h1{font-size:20px}h2{font-size:12px;color:var(--text-dim);margin:32px 0 8px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.tile{border:1px solid var(--dim);background:var(--panel);padding:12px 16px}
.tile b{display:block;font-size:34px;font-weight:500;letter-spacing:.04em}
.tile span,.note,.legend{color:var(--text-dim);text-transform:uppercase;letter-spacing:.1em;
font-size:10px}
.panel{border:1px solid var(--dim);background:var(--panel);padding:12px}
svg{width:100%;height:auto;display:block}
.grid{stroke:var(--grid)}.axis{stroke:var(--dim)}.tick{fill:var(--text-dim);font-size:10px}
.lbl{fill:var(--text)}
.solid{fill:var(--line)}.outline{fill:none;stroke:var(--line)}.hatch{fill:url(#hatch);
stroke:var(--dim)}
polyline{fill:none;stroke:var(--line);stroke-width:1.5}.l-dash{stroke-dasharray:6 4}
.l-dot{stroke-dasharray:1.5 4;stroke-linecap:round}
table{width:100%;border-collapse:collapse}th,td{padding:5px 8px;border-bottom:1px solid
var(--grid);text-align:right}th{color:var(--text-dim);font-weight:400;text-transform:uppercase;
letter-spacing:.08em;font-size:10px}th:first-child,td:first-child{text-align:left}
.legend i{display:inline-block;width:18px;height:9px;margin:0 6px 0 16px;vertical-align:-1px}
.table-wrap{overflow-x:auto}
</style></head><body><main>
<svg width="0" height="0" style="position:absolute"><defs><pattern id="hatch" width="5" height="5"
patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="5"
stroke="rgba(255,255,255,.85)" stroke-width="1.5"/></pattern></defs></svg>
<h1>Backtest {{ year }} · {{ battery_id }} · {{ zone }}</h1>
<p class="note">{{ capacity }} kWh / {{ power }} kW · round trip {{ rte }}% · price source
{{ source }} · config {{ config_hash }} · {{ days }} days · {{ violations }} constraint
violations{% if skipped %} · {{ skipped|length }} days skipped for data gaps{% endif %}</p>
<div class="tiles">
<div class="tile"><span>Optimizer revenue</span><b>€ {{ f(h.optimizer_eur) }}</b>
<span>€ {{ f(h.eur_per_mwh_capacity) }} per MWh capacity</span></div>
<div class="tile"><span>Uplift vs naive rule</span><b>{{ uplift(h.uplift_vs_naive_pct) }}</b>
<span>naive € {{ f(h.naive_eur) }}</span></div>
<div class="tile"><span>Full equivalent cycles</span><b>{{ f(h.cycles) }}</b>
<span>{{ f(h.cycles / days, 2) }} per day</span></div>
<div class="tile"><span>No-foresight share, %</span><b>{{ f(h.persistence_share_pct, 1) }}</b>
<span>persistence € {{ f(h.persistence_eur) }}</span></div>
</div>
<p class="note">Upper bound: the optimizer schedules against prices that are already cleared.
A real battery bids before the auction. The persistence row plans on yesterday's prices and
shows what survives without foresight.</p>
<h2>Monthly revenue</h2>
<div class="panel">{{ bars }}<p class="legend"><i class="solid" style="background:var(--line)">
</i>optimizer<i style="background:repeating-linear-gradient(45deg,var(--line) 0 1.5px,transparent
1.5px 5px);border:1px solid var(--dim)"></i>naive<i style="border:1px solid var(--line)"></i>
persistence</p></div>
<h2>Cumulative revenue</h2><div class="panel">{{ lines }}</div>
<h2>Year by strategy</h2><div class="panel table-wrap"><table>
<tr><th>Strategy</th><th>Revenue €</th><th>Peak savings €</th><th>Total €</th><th>€/kWh cap.</th>
<th>Cycles</th><th>Spread €/MWh</th><th>Peak cut kW</th></tr>
{% for r in yearly %}<tr><td>{{ r.strategy }}</td><td>{{ f(r.revenue_eur) }}</td>
<td>{{ f(r.peak_savings_eur) }}</td><td>{{ f(r.total_eur) }}</td>
<td>{{ f(r.eur_per_kwh_capacity, 2) }}</td><td>{{ f(r.cycles, 1) }}</td>
<td>{{ f(r.spread_captured_eur_mwh, 1) }}</td><td>{{ f(r.peak_reduction_kw, 1) }}</td></tr>
{% endfor %}</table></div>
<h2>Special days</h2><div class="panel table-wrap"><table>
<tr><th>Day</th><th>Date</th><th>Value</th><th>Price min</th><th>Price max</th><th>Optimizer €</th>
<th>Naive €</th></tr>
{% for d in special %}<tr><td>{{ d.label }}</td><td>{{ d.date }}</td>
<td>{{ f(d.value, 2) }} {{ d.unit }}</td><td>{{ f(d.price_min, 2) }}</td>
<td>{{ f(d.price_max, 2) }}</td><td>{{ f(d.optimizer_eur, 2) }}</td><td>{{ f(d.naive_eur, 2) }}</td>
</tr>{% endfor %}</table></div>
<h2>Monthly table</h2><div class="panel table-wrap"><table>
<tr><th>Month</th><th>Optimizer €</th><th>Naive €</th><th>Persistence €</th><th>Cycles</th></tr>
{% for m in months %}<tr><td>{{ m.month }}</td><td>{{ f(m.optimizer) }}</td><td>{{ f(m.naive) }}
</td><td>{{ f(m.persistence) }}</td><td>{{ f(m.cycles, 1) }}</td></tr>{% endfor %}</table></div>
{% if skipped %}<h2>Skipped days</h2><p>{{ skipped|join(", ") }}</p>{% endif %}
</main></body></html>
""")


def render(summary: dict, daily: pd.DataFrame, monthly: pd.DataFrame, yearly: pd.DataFrame) -> str:
    wide = monthly.pivot(index="month", columns="strategy", values="total_eur")
    cycles = monthly[monthly["strategy"] == "optimizer"].set_index("month")["cycles"]
    months = [
        {"month": m, "optimizer": r["optimizer"], "naive": r["naive"],
         "persistence": r["persistence"], "cycles": cycles[str(m)]}
        for m, r in wide.iterrows()
    ]
    battery = summary["config"]["battery"]
    return _TEMPLATE.render(
        year=summary["year"], battery_id=battery["id"], zone=summary["config"]["market"]["zone"],
        capacity=_fmt(battery["capacity_kwh"]), power=_fmt(battery["max_discharge_kw"]),
        rte=_fmt(battery["eta_charge"] * battery["eta_discharge"] * 100, 1),
        source=summary["source"], config_hash=summary["config_hash"], days=summary["days"],
        violations=len(summary["violations"]), skipped=summary["skipped_days"],
        h=summary["headline"], f=_fmt, uplift=uplift_text, bars=monthly_bars(monthly),
        lines=cumulative_lines(daily),
        yearly=yearly[yearly["strategy"] != "idle"].to_dict("records"),
        special=summary["special_days"], months=months,
    )

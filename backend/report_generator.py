"""
Smart PUC -- Report Generator
==============================

Standalone module for generating emission reports in multiple formats:

- **CSV** -- vehicle emission history and fleet analytics exports.
- **HTML** -- printable single-page reports with inline CSS and SVG charts.
- **LaTeX** -- publication-ready tables using booktabs + tabularx for
  direct inclusion in IEEE / Springer papers.

Usage::

    from backend.report_generator import ReportGenerator
    rg = ReportGenerator()
    csv_text = rg.generate_vehicle_csv("MH-01-AB-1234", records)
    html_text = rg.generate_vehicle_report_html("MH-01-AB-1234", profile, records, stats)
    latex_text = rg.generate_latex_table(data, "Emission summary", "tab:emissions")

Design note
-----------
All methods are pure functions (no I/O side-effects) that return strings.
The caller (typically a FastAPI route in ``main.py``) is responsible for
writing the string to an HTTP response or to disk.  This keeps the module
easy to test and free of web-framework dependencies.
"""

from __future__ import annotations

import csv
import html as _html
import io
import datetime
from typing import Any


class ReportGenerator:
    """Multi-format report generator for Smart PUC emission data."""

    # ─── CSV exports ────────────────────────────────────────────────────

    @staticmethod
    def generate_vehicle_csv(vehicle_id: str, records: list[dict]) -> str:
        """Generate a CSV string of emission history for a single vehicle.

        Parameters
        ----------
        vehicle_id : str
            Vehicle registration number (included as a column).
        records : list[dict]
            List of telemetry dicts, each expected to contain at minimum
            ``observed_at`` and a nested ``reading`` dict with gas values.

        Returns
        -------
        str
            UTF-8 CSV text with header row.
        """
        buf = io.StringIO()
        writer = csv.writer(buf)

        # Header
        writer.writerow([
            "vehicle_id", "timestamp", "co2_gkm", "nox_ppm", "co_ppm",
            "hc_ppm", "pm25_ugm3", "ces_score", "is_violation", "onchain_tx",
        ])

        for rec in records:
            reading = rec.get("reading", {})
            ts = rec.get("observed_at", "")
            if isinstance(ts, (int, float)):
                ts = datetime.datetime.utcfromtimestamp(ts).isoformat()
            writer.writerow([
                vehicle_id,
                ts,
                reading.get("co2_gkm", ""),
                reading.get("nox_ppm", ""),
                reading.get("co_ppm", ""),
                reading.get("hc_ppm", ""),
                reading.get("pm25_ugm3", ""),
                reading.get("ces_score", ""),
                rec.get("is_violation", 0),
                rec.get("onchain_tx", ""),
            ])

        return buf.getvalue()

    @staticmethod
    def generate_fleet_csv(fleet_data: list[dict]) -> str:
        """Generate a CSV of fleet-level analytics.

        Parameters
        ----------
        fleet_data : list[dict]
            Each dict represents one vehicle's aggregate stats.  Expected
            keys: ``vehicle_id``, ``avg_ces``, ``total_readings``,
            ``violation_count``, ``tier``, ``last_reading_at``.

        Returns
        -------
        str
            UTF-8 CSV text with header row.
        """
        buf = io.StringIO()
        writer = csv.writer(buf)

        writer.writerow([
            "vehicle_id", "avg_ces", "total_readings", "violation_count",
            "tier", "last_reading_at",
        ])

        for entry in fleet_data:
            last_at = entry.get("last_reading_at", "")
            if isinstance(last_at, (int, float)):
                last_at = datetime.datetime.utcfromtimestamp(last_at).isoformat()
            writer.writerow([
                entry.get("vehicle_id", ""),
                _fmt_float(entry.get("avg_ces")),
                entry.get("total_readings", 0),
                entry.get("violation_count", 0),
                entry.get("tier", "Unclassified"),
                last_at,
            ])

        return buf.getvalue()

    # ─── HTML report ────────────────────────────────────────────────────

    @staticmethod
    def generate_vehicle_report_html(
        vehicle_id: str,
        profile: dict[str, Any],
        records: list[dict],
        stats: dict[str, Any],
    ) -> str:
        """Generate a printable HTML report for a single vehicle.

        The output is a self-contained HTML document with inline CSS
        optimised for A4 printing.  It includes:

        - Vehicle specification summary
        - Emission history table (last N readings)
        - CES trend chart rendered as inline SVG
        - Compliance summary and certificate status

        Parameters
        ----------
        vehicle_id : str
            Vehicle registration number.
        profile : dict
            Vehicle metadata (``fuel_type``, ``engine_cc``, ``mfg_year``,
            ``bs_norm``, ``owner_name``, etc.).
        records : list[dict]
            Telemetry records (most recent first).
        stats : dict
            Aggregate statistics: ``avg_ces``, ``min_ces``, ``max_ces``,
            ``violation_count``, ``total_readings``, ``tier``,
            ``cert_status``, ``cert_expiry``.

        Returns
        -------
        str
            Complete HTML document as a string.
        """
        esc = _html.escape

        # --- Build CES trend SVG ---
        ces_values = []
        for r in reversed(records):  # oldest first for chart
            reading = r.get("reading", {})
            val = reading.get("ces_score")
            if val is not None:
                ces_values.append(float(val))

        svg_chart = _build_ces_svg(ces_values) if ces_values else (
            '<p style="color:#888;text-align:center;">No CES data available for chart.</p>'
        )

        # --- Build readings table rows ---
        table_rows = []
        for rec in records[:50]:  # cap at 50 rows
            reading = rec.get("reading", {})
            ts = rec.get("observed_at", "")
            if isinstance(ts, (int, float)):
                ts = datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
            violation_cls = ' class="violation"' if rec.get("is_violation") else ""
            table_rows.append(
                f"<tr{violation_cls}>"
                f"<td>{esc(str(ts))}</td>"
                f"<td>{_fmt_float(reading.get('co2_gkm'))}</td>"
                f"<td>{_fmt_float(reading.get('nox_ppm'))}</td>"
                f"<td>{_fmt_float(reading.get('co_ppm'))}</td>"
                f"<td>{_fmt_float(reading.get('hc_ppm'))}</td>"
                f"<td>{_fmt_float(reading.get('pm25_ugm3'))}</td>"
                f"<td><strong>{_fmt_float(reading.get('ces_score'))}</strong></td>"
                f"</tr>"
            )
        readings_html = "\n".join(table_rows) if table_rows else (
            '<tr><td colspan="7" style="text-align:center;">No readings recorded.</td></tr>'
        )

        # --- Compliance badge ---
        tier = stats.get("tier", "Unclassified")
        tier_colour = {
            "Green": "#27ae60", "Yellow": "#f39c12",
            "Orange": "#e67e22", "Red": "#e74c3c",
        }.get(tier, "#95a5a6")

        cert_status = stats.get("cert_status", "Unknown")
        cert_colour = "#27ae60" if cert_status == "Valid" else "#e74c3c"

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Smart PUC Report -- {esc(vehicle_id)}</title>
<style>
  @page {{ size: A4; margin: 15mm; }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Segoe UI', Arial, sans-serif; font-size: 11pt;
         color: #222; line-height: 1.4; padding: 20px; max-width: 210mm;
         margin: 0 auto; }}
  h1 {{ font-size: 18pt; border-bottom: 2px solid #2c3e50; padding-bottom: 6px;
        margin-bottom: 12px; }}
  h2 {{ font-size: 13pt; color: #2c3e50; margin: 16px 0 8px; }}
  .meta-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 6px 24px;
                margin-bottom: 16px; }}
  .meta-grid dt {{ font-weight: 600; color: #555; }}
  .meta-grid dd {{ margin-left: 0; }}
  .badge {{ display: inline-block; padding: 2px 10px; border-radius: 4px;
            color: #fff; font-weight: 700; font-size: 10pt; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 9pt;
           margin-bottom: 16px; }}
  th, td {{ border: 1px solid #ccc; padding: 4px 8px; text-align: right; }}
  th {{ background: #2c3e50; color: #fff; text-align: center; }}
  tr.violation {{ background: #fdecea; }}
  .chart-container {{ margin: 12px 0; text-align: center; }}
  .summary-box {{ background: #f7f9fc; border: 1px solid #d0d7de;
                   border-radius: 6px; padding: 12px; margin-bottom: 16px; }}
  .footer {{ margin-top: 24px; font-size: 8pt; color: #888;
             border-top: 1px solid #ccc; padding-top: 6px; text-align: center; }}
  @media print {{
    body {{ padding: 0; }}
    .no-print {{ display: none; }}
  }}
</style>
</head>
<body>
<h1>Smart PUC Emission Report</h1>

<h2>Vehicle Details</h2>
<dl class="meta-grid">
  <dt>Registration</dt><dd>{esc(vehicle_id)}</dd>
  <dt>Fuel Type</dt><dd>{esc(str(profile.get('fuel_type', 'N/A')))}</dd>
  <dt>Engine</dt><dd>{esc(str(profile.get('engine_cc', 'N/A')))} cc</dd>
  <dt>BS Norm</dt><dd>{esc(str(profile.get('bs_norm', 'N/A')))}</dd>
  <dt>Mfg. Year</dt><dd>{esc(str(profile.get('mfg_year', 'N/A')))}</dd>
  <dt>Owner</dt><dd>{esc(str(profile.get('owner_name', 'N/A')))}</dd>
</dl>

<h2>Compliance Summary</h2>
<div class="summary-box">
  <p><strong>Tier:</strong>
     <span class="badge" style="background:{tier_colour};">{esc(tier)}</span></p>
  <p><strong>Certificate:</strong>
     <span class="badge" style="background:{cert_colour};">{esc(cert_status)}</span>
     {(' -- expires ' + esc(str(stats.get('cert_expiry', '')))) if stats.get('cert_expiry') else ''}
  </p>
  <p><strong>Avg CES:</strong> {_fmt_float(stats.get('avg_ces'))}
     &nbsp;|&nbsp; <strong>Min:</strong> {_fmt_float(stats.get('min_ces'))}
     &nbsp;|&nbsp; <strong>Max:</strong> {_fmt_float(stats.get('max_ces'))}</p>
  <p><strong>Total Readings:</strong> {stats.get('total_readings', 0)}
     &nbsp;|&nbsp; <strong>Violations:</strong> {stats.get('violation_count', 0)}</p>
</div>

<h2>CES Trend</h2>
<div class="chart-container">
{svg_chart}
</div>

<h2>Emission History (last {min(len(records), 50)} readings)</h2>
<table>
<thead>
<tr>
  <th>Timestamp</th><th>CO2 (g/km)</th><th>NOx (ppm)</th>
  <th>CO (ppm)</th><th>HC (ppm)</th><th>PM2.5 (ug/m3)</th><th>CES</th>
</tr>
</thead>
<tbody>
{readings_html}
</tbody>
</table>

<div class="footer">
  Generated by Smart PUC Emission Monitoring System &mdash;
  {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
</div>
</body>
</html>"""

    # ─── LaTeX table generators ─────────────────────────────────────────

    @staticmethod
    def generate_latex_table(
        data: list[dict],
        caption: str,
        label: str,
        columns: list[str] | None = None,
        col_headers: list[str] | None = None,
    ) -> str:
        r"""Generate a LaTeX table using booktabs + tabularx.

        Parameters
        ----------
        data : list[dict]
            Each dict is one row.  Keys become columns unless *columns*
            is specified.
        caption : str
            Table caption text.
        label : str
            LaTeX label for ``\\ref{}`` (e.g. ``"tab:emissions"``).
        columns : list[str] | None
            Ordered list of dict keys to include.  Defaults to all keys
            of the first row.
        col_headers : list[str] | None
            Human-readable column headers.  Must match length of
            *columns*.  Defaults to the key names.

        Returns
        -------
        str
            Complete LaTeX table environment ready for ``\\input{}``.
        """
        if not data:
            return f"% Empty table: {label}\n"

        if columns is None:
            columns = list(data[0].keys())
        if col_headers is None:
            col_headers = [_latex_escape(c.replace("_", " ").title()) for c in columns]
        else:
            col_headers = [_latex_escape(h) for h in col_headers]

        n = len(columns)
        col_spec = "l" + "X" * (n - 1) if n > 1 else "X"

        header_row = " & ".join(f"\\textbf{{{h}}}" for h in col_headers)

        body_rows = []
        for row in data:
            cells = []
            for col in columns:
                val = row.get(col, "")
                if isinstance(val, float):
                    cells.append(f"{val:.3f}")
                else:
                    cells.append(_latex_escape(str(val)))
            body_rows.append(" & ".join(cells) + r" \\")

        body = "\n".join(body_rows)

        return (
            f"\\begin{{table}}[htbp]\n"
            f"\\centering\n"
            f"\\caption{{{_latex_escape(caption)}}}\n"
            f"\\label{{{label}}}\n"
            f"\\begin{{tabularx}}{{\\linewidth}}{{{col_spec}}}\n"
            f"\\toprule\n"
            f"{header_row} \\\\\n"
            f"\\midrule\n"
            f"{body}\n"
            f"\\bottomrule\n"
            f"\\end{{tabularx}}\n"
            f"\\end{{table}}\n"
        )

    @staticmethod
    def generate_comparison_latex(vehicles_data: list[dict]) -> str:
        r"""Generate a LaTeX comparison table of multiple vehicles.

        Each entry in *vehicles_data* should contain:
        ``vehicle_id``, ``fuel_type``, ``bs_norm``, ``avg_ces``,
        ``avg_co2``, ``avg_nox``, ``violation_rate``, ``tier``.

        Returns
        -------
        str
            LaTeX table environment with booktabs formatting.
        """
        if not vehicles_data:
            return "% Empty comparison table\n"

        columns = [
            "vehicle_id", "fuel_type", "bs_norm", "avg_ces",
            "avg_co2", "avg_nox", "violation_rate", "tier",
        ]
        headers = [
            "Vehicle ID", "Fuel", "BS Norm", "Avg CES",
            "CO$_2$ (g/km)", "NO$_x$ (ppm)", "Violation \\%", "Tier",
        ]

        header_row = " & ".join(f"\\textbf{{{h}}}" for h in headers)

        body_rows = []
        for v in vehicles_data:
            cells = []
            for col in columns:
                val = v.get(col, "")
                if isinstance(val, float):
                    if col == "violation_rate":
                        cells.append(f"{val * 100:.1f}")
                    else:
                        cells.append(f"{val:.2f}")
                else:
                    cells.append(_latex_escape(str(val)))
            body_rows.append(" & ".join(cells) + r" \\")

        body = "\n".join(body_rows)

        return (
            "\\begin{table}[htbp]\n"
            "\\centering\n"
            "\\caption{Multi-vehicle emission comparison}\n"
            "\\label{tab:vehicle_comparison}\n"
            "\\begin{tabularx}{\\linewidth}{lllXXXXl}\n"
            "\\toprule\n"
            f"{header_row} \\\\\n"
            "\\midrule\n"
            f"{body}\n"
            "\\bottomrule\n"
            "\\end{tabularx}\n"
            "\\end{table}\n"
        )


# ─── Module-level helpers ───────────────────────────────────────────────

def _fmt_float(val: Any, decimals: int = 2) -> str:
    """Format a numeric value for display, returning '' for None."""
    if val is None:
        return ""
    try:
        return f"{float(val):.{decimals}f}"
    except (TypeError, ValueError):
        return str(val)


def _latex_escape(text: str) -> str:
    """Escape LaTeX special characters in plain text."""
    replacements = [
        ("\\", r"\textbackslash{}"),
        ("{", r"\{"),
        ("}", r"\}"),
        ("&", r"\&"),
        ("%", r"\%"),
        ("$", r"\$"),
        ("#", r"\#"),
        ("_", r"\_"),
        ("~", r"\textasciitilde{}"),
        ("^", r"\textasciicircum{}"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def _build_ces_svg(values: list[float], width: int = 600, height: int = 180) -> str:
    """Build a simple inline SVG line chart for CES trend.

    Parameters
    ----------
    values : list[float]
        CES scores in chronological order (oldest first).
    width, height : int
        SVG canvas dimensions in pixels.

    Returns
    -------
    str
        ``<svg>`` element suitable for embedding in HTML.
    """
    if not values:
        return ""

    pad_x, pad_y = 50, 20
    plot_w = width - 2 * pad_x
    plot_h = height - 2 * pad_y

    v_min = max(0, min(values) - 5)
    v_max = min(100, max(values) + 5)
    v_range = v_max - v_min if v_max != v_min else 1

    n = len(values)
    step_x = plot_w / max(n - 1, 1)

    def pt(i: int, v: float) -> tuple[float, float]:
        x = pad_x + i * step_x
        y = pad_y + plot_h - (v - v_min) / v_range * plot_h
        return x, y

    # Build polyline points
    points = " ".join(f"{pt(i, v)[0]:.1f},{pt(i, v)[1]:.1f}" for i, v in enumerate(values))

    # Threshold line at CES = 40 (fail threshold)
    if v_min <= 40 <= v_max:
        thresh_y = pad_y + plot_h - (40 - v_min) / v_range * plot_h
        thresh_line = (
            f'<line x1="{pad_x}" y1="{thresh_y:.1f}" '
            f'x2="{pad_x + plot_w}" y2="{thresh_y:.1f}" '
            f'stroke="#e74c3c" stroke-width="1" stroke-dasharray="6,3"/>'
            f'<text x="{pad_x - 4}" y="{thresh_y:.1f}" '
            f'text-anchor="end" font-size="9" fill="#e74c3c">40</text>'
        )
    else:
        thresh_line = ""

    # Y-axis labels
    y_labels = ""
    for tick in range(int(v_min), int(v_max) + 1, 10):
        ty = pad_y + plot_h - (tick - v_min) / v_range * plot_h
        y_labels += (
            f'<text x="{pad_x - 6}" y="{ty + 3:.1f}" '
            f'text-anchor="end" font-size="9" fill="#666">{tick}</text>'
            f'<line x1="{pad_x}" y1="{ty:.1f}" '
            f'x2="{pad_x + plot_w}" y2="{ty:.1f}" '
            f'stroke="#eee" stroke-width="0.5"/>'
        )

    # Colour the line based on average
    avg = sum(values) / len(values)
    if avg >= 70:
        line_colour = "#27ae60"
    elif avg >= 40:
        line_colour = "#f39c12"
    else:
        line_colour = "#e74c3c"

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" '
        f'style="background:#fafafa;border:1px solid #ddd;border-radius:4px;">\n'
        f'{y_labels}\n'
        f'{thresh_line}\n'
        f'<polyline points="{points}" fill="none" '
        f'stroke="{line_colour}" stroke-width="2" stroke-linejoin="round"/>\n'
        f'<text x="{width // 2}" y="{height - 2}" text-anchor="middle" '
        f'font-size="9" fill="#888">Reading index (oldest to newest)</text>\n'
        f'<text x="{pad_x - 6}" y="{pad_y - 4}" text-anchor="end" '
        f'font-size="9" fill="#888">CES</text>\n'
        f'</svg>'
    )

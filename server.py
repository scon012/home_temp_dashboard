"""Serve and visualize temperature readings from local CSV log files.

The module provides a dependency-free Python HTTP server and an embedded
responsive HTML dashboard.  It supports both wide CSV files (timestamp plus
one column per location) and long CSV files (timestamp, location, temperature).
Chart.js is loaded by the browser from a CDN.
"""

from collections import defaultdict
import csv
from datetime import datetime
import html
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import socket
import statistics
from urllib.parse import parse_qs, urlparse

# Define the allowed files (Display Name -> Full Windows Path)
FILES = {
    "18_hue_temperature_log.csv": (
        r"C:\temperature_monitor\18_hue_temperature_log.csv"
    ),
    "1A_hue_temperature_log.csv": (
        r"C:\temperature_monitor\1A_hue_temperature_log.csv"
    ),
}
"""Mapping of user-visible CSV names to their absolute Windows paths."""

DEFAULT_FILE = "18_hue_temperature_log.csv"
PORT = 8000
"""Default CSV selection and HTTP listening port."""


def parse_timestamp(ts_str):
  """Parse a timestamp using ISO 8601 and a set of common log formats.

  Args:
    ts_str: Timestamp text to parse.

  Returns:
    A :class:`datetime.datetime` when parsing succeeds, otherwise ``None``.
  """
  s = ts_str.strip()
  if not s:
    return None

  iso_candidate = s.replace("Z", "+00:00")
  try:
    return datetime.fromisoformat(iso_candidate)
  except ValueError:
    pass

  formats = [
      "%Y-%m-%d %H:%M:%S",
      "%Y-%m-%d %H:%M",
      "%Y/%m/%d %H:%M:%S",
      "%Y/%m/%d %H:%M",
      "%d/%m/%Y %H:%M:%S",
      "%d/%m/%Y %H:%M",
      "%m/%d/%Y %H:%M:%S",
      "%m/%d/%Y %H:%M",
      "%d-%m-%Y %H:%M:%S",
      "%d-%m-%Y %H:%M",
      "%Y-%m-%d",
      "%d/%m/%Y",
      "%m/%d/%Y",
  ]
  for fmt in formats:
    try:
      return datetime.strptime(s, fmt)
    except ValueError:
      continue
  return None


def hex_to_rgba(hex_color, alpha):
  """Convert a hexadecimal CSS color to an ``rgba()`` CSS string."""
  h = hex_color.lstrip("#")
  r, g, b = tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))
  return f"rgba({r}, {g}, {b}, {alpha})"


def parse_csv_for_charts(rows):
  """Normalize CSV rows into Chart.js dataset payloads.

  The parser recognizes two layouts:

  * Wide format: ``Timestamp, Location A, Location B, ...``.
  * Long format: ``Timestamp, Location/Sensor, Temperature``.

  Args:
    rows: CSV rows, including the header row.

  Returns:
    A dictionary containing ``hourly`` and ``daily`` payloads.  Each payload
    has ``labels`` and Chart.js ``datasets`` lists.  The hourly dataset contains
    mean temperatures for each hour; the daily dataset contains mean, median,
    minimum, and maximum temperatures for each day.

    Malformed timestamps or non-numeric temperatures are omitted.  An empty
    pair of payloads is returned when no usable data rows exist.
  """
  empty_payload = {
      "hourly": {"labels": [], "datasets": []},
      "daily": {"labels": [], "datasets": []},
  }
  if len(rows) < 2:
    return empty_payload

  headers = [h.strip() for h in rows[0]]
  data_rows = [r for r in rows[1:] if len(r) >= 2]
  if not data_rows:
    return empty_payload

  colors = [
      "#e6194b",
      "#3cb44b",
      "#4363d8",
      "#f58231",
      "#911eb4",
      "#008080",
      "#f032e6",
      "#9a6324",
      "#800000",
      "#808000",
      "#469990",
      "#000075",
      "#e6beff",
      "#fabebe",
      "#aaffc3",
  ]

  is_long_format = False
  if len(headers) >= 3 and len(data_rows[0]) >= 3:
    try:
      float(data_rows[0][1])
    except ValueError:
      try:
        float(data_rows[0][2])
        is_long_format = True
      except ValueError:
        pass

  locations_seen = []
  location_set = set()

  hourly_buckets = defaultdict(list)
  daily_buckets = defaultdict(list)

  hour_keys_map = {}
  day_keys_map = {}

  def record_reading(ts_raw, loc, val):
    if loc not in location_set:
      location_set.add(loc)
      locations_seen.append(loc)

    dt = parse_timestamp(ts_raw)
    if dt is not None:
      hour_label = dt.strftime("%Y-%m-%d %H:00")
      hour_sort = dt.strftime("%Y-%m-%d %H")
      day_label = dt.strftime("%Y-%m-%d")
      day_sort = day_label
    else:
      parts = ts_raw.strip().split()
      day_label = parts[0] if parts else ts_raw.strip()
      hour_part = (
          parts[1][:2] + ":00" if len(parts) > 1 and len(parts[1]) >= 2 else ""
      )
      hour_label = f"{day_label} {hour_part}".strip()
      hour_sort = hour_label
      day_sort = day_label

    hour_keys_map[hour_label] = hour_sort
    day_keys_map[day_label] = day_sort

    hourly_buckets[(hour_label, loc)].append(val)
    daily_buckets[(day_label, loc)].append(val)

  if is_long_format:
    for row in data_rows:
      if len(row) < 3:
        continue
      ts = row[0].strip()
      loc = row[1].strip()
      try:
        val = float(row[2].strip())
      except ValueError:
        continue
      record_reading(ts, loc, val)
  else:
    for row in data_rows:
      ts = row[0].strip()
      for col_idx in range(1, len(headers)):
        if col_idx >= len(row):
          continue
        loc = headers[col_idx] or f"Sensor {col_idx}"
        try:
          val = float(row[col_idx].strip())
        except ValueError:
          continue
        record_reading(ts, loc, val)

  sorted_hours = sorted(
      hour_keys_map.keys(), key=lambda k: hour_keys_map[k]
  )
  sorted_days = sorted(day_keys_map.keys(), key=lambda k: day_keys_map[k])

  # --- Build Chart 1: Hourly Average ---
  hourly_datasets = []
  for idx, loc in enumerate(locations_seen):
    color = colors[idx % len(colors)]
    series_data = []
    for h in sorted_hours:
      vals = hourly_buckets.get((h, loc))
      series_data.append(round(statistics.mean(vals), 2) if vals else None)

    hourly_datasets.append({
        "label": loc,
        "data": series_data,
        "borderColor": color,
        "backgroundColor": color,
        "borderWidth": 2.5,
        "pointRadius": 2,
        "pointHitRadius": 12,
        "tension": 0.25,
        "spanGaps": True,
    })

  # --- Build Chart 2: Daily Median, Mean, and High / Low ---
  daily_datasets = []
  for idx, loc in enumerate(locations_seen):
    color = colors[idx % len(colors)]
    fill_color = hex_to_rgba(color, 0.14)

    means = []
    medians = []
    highs = []
    lows = []

    for d in sorted_days:
      vals = daily_buckets.get((d, loc))
      if vals:
        means.append(round(statistics.mean(vals), 2))
        medians.append(round(statistics.median(vals), 2))
        highs.append(round(max(vals), 2))
        lows.append(round(min(vals), 2))
      else:
        means.append(None)
        medians.append(None)
        highs.append(None)
        lows.append(None)

    daily_datasets.append({
        "label": f"{loc} (Low)",
        "locationGroup": loc,
        "metricType": "Low",
        "data": lows,
        "borderColor": hex_to_rgba(color, 0.45),
        "backgroundColor": "transparent",
        "borderWidth": 1,
        "pointRadius": 0,
        "pointHitRadius": 10,
        "tension": 0.2,
        "spanGaps": True,
        "fill": False,
    })
    daily_datasets.append({
        "label": f"{loc} (High)",
        "locationGroup": loc,
        "metricType": "High",
        "data": highs,
        "borderColor": hex_to_rgba(color, 0.45),
        "backgroundColor": fill_color,
        "borderWidth": 1,
        "pointRadius": 0,
        "pointHitRadius": 10,
        "tension": 0.2,
        "spanGaps": True,
        "fill": "-1",
    })
    daily_datasets.append({
        "label": f"{loc} (Median)",
        "locationGroup": loc,
        "metricType": "Median",
        "data": medians,
        "borderColor": color,
        "backgroundColor": color,
        "borderWidth": 2,
        "borderDash": [5, 5],
        "pointRadius": 2.5,
        "pointHitRadius": 12,
        "tension": 0.2,
        "spanGaps": True,
        "fill": False,
    })
    daily_datasets.append({
        "label": f"{loc} (Mean)",
        "locationGroup": loc,
        "metricType": "Mean",
        "data": means,
        "borderColor": color,
        "backgroundColor": color,
        "borderWidth": 2.5,
        "pointRadius": 3,
        "pointHitRadius": 12,
        "tension": 0.2,
        "spanGaps": True,
        "fill": False,
    })

  return {
      "hourly": {"labels": sorted_hours, "datasets": hourly_datasets},
      "daily": {"labels": sorted_days, "datasets": daily_datasets},
  }


class CSVHandler(BaseHTTPRequestHandler):
  """Handle dashboard requests and serve the temperature log interface.

  Only ``GET /`` is implemented.  A ``file`` query parameter selects an entry
  in :data:`FILES`; an unknown or missing value falls back to :data:`DEFAULT_FILE`.
  The response is a complete HTML document with embedded CSS and JavaScript.
  """

  def do_GET(self):
    parsed_url = urlparse(self.path)

    if parsed_url.path != "/":
      self.send_error(404, "File not found")
      return

    query_params = parse_qs(parsed_url.query)
    selected_name = query_params.get("file", [DEFAULT_FILE])[0]

    if selected_name not in FILES:
      selected_name = DEFAULT_FILE

    filepath = FILES[selected_name]

    rows = []
    error_msg = None
    if os.path.exists(filepath):
      try:
        with open(filepath, mode="r", encoding="utf-8-sig") as f:
          rows = list(csv.reader(f))
      except Exception as e:
        error_msg = f"Error reading file: {str(e)}"
    else:
      error_msg = f"Error: Could not find {filepath}"

    charts_payload = (
        parse_csv_for_charts(rows)
        if not error_msg
        else {
            "hourly": {"labels": [], "datasets": []},
            "daily": {"labels": [], "datasets": []},
        }
    )
    charts_json = json.dumps(charts_payload)

    options_html = []
    for name in FILES.keys():
      selected_attr = " selected" if name == selected_name else ""
      options_html.append(
          f'<option value="{html.escape(name)}"{selected_attr}>'
          f"{html.escape(name)}</option>"
      )

    # Build HTML Table with sortable title area + multi-select filter trigger per column
    table_html = []
    if rows:
      table_html.append('<table id="data-table"><thead><tr>')
      for col_idx, cell in enumerate(rows[0]):
        col_label = html.escape(cell)
        table_html.append(
            f'<th data-col="{col_idx}">'
            f'<div class="th-inner">'
            f'<div class="th-sort-area" onclick="handleSort({col_idx}, event)"'
            ' role="button" tabindex="0">'
            f"<span>{col_label}</span>"
            '<span class="sort-badge" aria-hidden="true"></span>'
            "</div>"
            f'<button type="button" class="filter-trigger" id="filter-btn-{col_idx}" '
            f'onclick="toggleFilterMenu({col_idx}, event)" '
            f'aria-label="Filter {col_label}" title="Filter {col_label}">'
            '<svg width="14" height="14" viewBox="0 0 16 16"'
            ' fill="currentColor">'
            '<path d="M1.5 2.5A.5.5 0 0 1 2 2h12a.5.5 0 0 1 .4.8L10'
            " 8.67V13.5a.5.5 0 0 1-.76.43l-2.5-1.5A.5.5 0 0 1 6.5 12V8.67L1.6"
            ' 2.8a.5.5 0 0 1-.1-.3z"/>'
            "</svg>"
            "</button>"
            "</div>"
            "</th>"
        )
      table_html.append("</tr></thead><tbody>")
      for row in rows[1:]:
        table_html.append("<tr>")
        for cell in row:
          table_html.append(f"<td>{html.escape(cell)}</td>")
        table_html.append("</tr>")
      table_html.append("</tbody></table>")

    self.send_response(200)
    self.send_header("Content-type", "text/html; charset=utf-8")
    self.end_headers()

    page_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <title>Hue Temperature Monitor</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {{
            --bg: #f2f4f7;
            --card: #ffffff;
            --primary: #0969da;
            --primary-soft: #ddf4ff;
            --border: #d0d7de;
            --text: #1f2328;
            --muted: #59636e;
        }}
        * {{ box-sizing: border-box; -webkit-tap-highlight-color: transparent; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            margin: 0;
            padding: env(safe-area-inset-top, 12px) env(safe-area-inset-right, 16px) env(safe-area-inset-bottom, 16px) env(safe-area-inset-left, 16px);
            background-color: var(--bg);
            color: var(--text);
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
            padding-top: 8px;
        }}
        
        /* Top Navigation Bar */
        .top-bar {{
            display: flex;
            flex-wrap: wrap;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
            margin-bottom: 14px;
            background: var(--card);
            padding: 12px 16px;
            border-radius: 12px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        }}
        .tabs {{
            display: flex;
            gap: 8px;
            flex: 1 1 auto;
        }}
        .tab-btn {{
            flex: 1 1 auto;
            max-width: 190px;
            min-height: 44px;
            padding: 10px 14px;
            font-size: 0.92rem;
            font-weight: 600;
            border: 1px solid var(--border);
            background: #f6f8fa;
            color: var(--muted);
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.15s ease;
            white-space: nowrap;
        }}
        .tab-btn.active {{
            background: var(--primary);
            color: #fff;
            border-color: var(--primary);
        }}
        .file-selector {{
            display: flex;
            align-items: center;
            gap: 8px;
            flex: 1 1 auto;
            justify-content: flex-end;
        }}
        select {{
            min-height: 44px;
            padding: 8px 12px;
            font-size: 16px;
            border-radius: 8px;
            border: 1px solid var(--border);
            background: #fff;
            color: var(--text);
            cursor: pointer;
            max-width: 100%;
        }}
        
        /* Content Cards */
        .tab-content {{
            display: none;
            background: var(--card);
            padding: 16px;
            border-radius: 12px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        }}
        .tab-content.active {{ display: block; }}
        
        /* Chart Layout & Toolbar */
        .chart-toolbar {{
            display: flex;
            flex-wrap: wrap;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
            margin-bottom: 10px;
        }}
        .chart-toolbar-right {{
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 8px;
        }}
        .chart-wrapper {{
            position: relative;
            height: 68vh;
            min-height: 360px;
            width: 100%;
        }}
        .hint {{
            font-size: 0.85rem;
            color: var(--muted);
            margin: 0;
        }}
        .legend-style-note {{
            display: inline-flex;
            flex-wrap: wrap;
            gap: 10px;
            font-size: 0.82rem;
            color: var(--muted);
            background: #f6f8fa;
            border: 1px solid var(--border);
            padding: 6px 10px;
            border-radius: 8px;
        }}
        
        /* Shared Action Buttons */
        .toggle-btn, .reset-btn {{
            min-height: 38px;
            padding: 6px 12px;
            font-size: 0.85rem;
            font-weight: 600;
            border-radius: 8px;
            border: 1px solid var(--border);
            background: #f6f8fa;
            color: var(--text);
            cursor: pointer;
            white-space: nowrap;
        }}
        .toggle-btn:hover {{
            background: #eef1f4;
        }}
        .toggle-btn.active {{
            background: var(--primary-soft);
            border-color: var(--primary);
            color: var(--primary);
        }}
        .reset-btn {{
            color: #cf222e;
            background: #fff;
        }}
        .reset-btn:disabled {{
            opacity: 0.4;
            cursor: default;
        }}
        
        /* Table Toolbar */
        .table-toolbar {{
            display: flex;
            flex-wrap: wrap;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
            margin-bottom: 12px;
        }}
        .toolbar-left {{
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 10px;
        }}
        .row-count-badge {{
            font-size: 0.82rem;
            font-weight: 600;
            color: var(--muted);
            background: #f6f8fa;
            border: 1px solid var(--border);
            padding: 5px 10px;
            border-radius: 999px;
        }}
        .sort-controls {{
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 8px;
        }}
        
        /* Touch-Friendly Table */
        .table-wrapper {{
            overflow: auto;
            -webkit-overflow-scrolling: touch;
            max-height: 70vh;
            border: 1px solid var(--border);
            border-radius: 8px;
        }}
        table {{
            border-collapse: collapse;
            width: 100%;
            font-size: 0.92rem;
        }}
        th, td {{
            border-bottom: 1px solid #e5e7eb;
            border-right: 1px solid #e5e7eb;
            padding: 0;
            text-align: left;
            white-space: nowrap;
        }}
        td {{
            padding: 11px 14px;
        }}
        th {{
            background-color: #f6f8fa;
            font-weight: 600;
            position: sticky;
            top: 0;
            z-index: 2;
            user-select: none;
        }}
        .th-inner {{
            display: flex;
            align-items: stretch;
            justify-content: space-between;
            min-height: 44px;
        }}
        .th-sort-area {{
            flex: 1;
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 10px 10px 10px 14px;
            cursor: pointer;
        }}
        .th-sort-area:hover {{
            background-color: #eef1f4;
        }}
        .filter-trigger {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 38px;
            min-height: 44px;
            border: none;
            border-left: 1px solid #e5e7eb;
            background: transparent;
            color: var(--muted);
            cursor: pointer;
            transition: background 0.15s, color 0.15s;
        }}
        .filter-trigger:hover {{
            background-color: #eef1f4;
            color: var(--text);
        }}
        .filter-trigger.has-filter {{
            background-color: var(--primary-soft);
            color: var(--primary);
        }}
        .sort-badge {{
            display: inline-flex;
            align-items: center;
            font-size: 0.78rem;
            font-weight: 700;
            color: var(--primary);
            background: var(--primary-soft);
            padding: 2px 6px;
            border-radius: 10px;
        }}
        .sort-badge:empty {{ display: none; }}
        tr:nth-child(even) {{ background-color: #fafbfc; }}

        /* Column Multi-Select Filter Popover */
        .filter-popover {{
            position: fixed;
            z-index: 1000;
            width: 260px;
            max-width: calc(100vw - 24px);
            background: #fff;
            border: 1px solid var(--border);
            border-radius: 12px;
            box-shadow: 0 10px 28px rgba(0, 0, 0, 0.18);
            display: none;
            flex-direction: column;
            overflow: hidden;
        }}
        .filter-popover.open {{
            display: flex;
        }}
        .filter-popover-header {{
            padding: 10px 12px;
            border-bottom: 1px solid #e5e7eb;
            background: #f6f8fa;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }}
        .filter-popover-title {{
            font-size: 0.85rem;
            font-weight: 700;
            color: var(--text);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .filter-search {{
            width: 100%;
            min-height: 36px;
            padding: 6px 10px;
            font-size: 16px;
            border: 1px solid var(--border);
            border-radius: 6px;
            outline: none;
        }}
        .filter-search:focus {{
            border-color: var(--primary);
        }}
        .filter-quick-actions {{
            display: flex;
            justify-content: space-between;
            gap: 8px;
        }}
        .filter-link-btn {{
            background: none;
            border: none;
            padding: 4px 2px;
            font-size: 0.82rem;
            font-weight: 600;
            color: var(--primary);
            cursor: pointer;
        }}
        .filter-options-list {{
            max-height: 240px;
            overflow-y: auto;
            -webkit-overflow-scrolling: touch;
            padding: 6px 0;
        }}
        .filter-option-item {{
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 9px 12px;
            min-height: 40px;
            font-size: 0.9rem;
            cursor: pointer;
            user-select: none;
        }}
        .filter-option-item:hover {{
            background: #f6f8fa;
        }}
        .filter-option-item input[type="checkbox"] {{
            width: 18px;
            height: 18px;
            accent-color: var(--primary);
            cursor: pointer;
            flex-shrink: 0;
        }}
        .filter-option-label {{
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}
        .filter-popover-footer {{
            padding: 8px 12px;
            border-top: 1px solid #e5e7eb;
            background: #f6f8fa;
            display: flex;
            justify-content: flex-end;
        }}
        .filter-done-btn {{
            min-height: 36px;
            padding: 6px 16px;
            font-size: 0.88rem;
            font-weight: 600;
            background: var(--primary);
            color: #fff;
            border: none;
            border-radius: 6px;
            cursor: pointer;
        }}

        /* Mobile Phone Portrait Tweaks */
        @media (max-width: 640px) {{
            .top-bar {{ padding: 10px 12px; }}
            .tabs {{ width: 100%; }}
            .tab-btn {{ max-width: none; font-size: 0.84rem; padding: 8px 8px; }}
            .file-selector {{ width: 100%; justify-content: space-between; }}
            .file-selector select {{ flex: 1; }}
            .tab-content {{ padding: 12px; }}
            .chart-wrapper {{ height: 60vh; min-height: 320px; }}
        }}
    </style>
</head>
<body>
<div class="container">
    <div class="top-bar">
        <div class="tabs">
            <button class="tab-btn active" onclick="switchTab('hourly', this)">Hourly Averages</button>
            <button class="tab-btn" onclick="switchTab('daily', this)">Daily Stats</button>
            <button class="tab-btn" onclick="switchTab('table', this)">Tabular Data</button>
        </div>

        <div class="file-selector">
            <label for="file-select" style="font-weight: 600; font-size: 0.9rem;">File:</label>
            <form method="GET" action="/" style="margin:0; flex: 1; display: flex; justify-content: flex-end;">
                <select id="file-select" name="file" onchange="this.form.submit()">
                    {"".join(options_html)}
                </select>
            </form>
        </div>
    </div>

    {f'<div style="color: #cf222e; background: #ffebe9; padding: 12px; border-radius: 8px; margin-bottom: 12px;">{html.escape(error_msg)}</div>' if error_msg else ''}

    <!-- Tab 1: Hourly Average Chart (Default) -->
    <div id="tab-hourly" class="tab-content active">
        <div class="chart-toolbar">
            <p class="hint">Showing hourly average temperatures. Tap any location in the key to toggle it on or off.</p>
            <div class="chart-toolbar-right">
                <button type="button" class="toggle-btn" onclick="setAllLocationsVisibility(hourlyChart, false)">Hide All</button>
                <button type="button" class="toggle-btn" onclick="setAllLocationsVisibility(hourlyChart, true)">Show All</button>
            </div>
        </div>
        <div class="chart-wrapper">
            <canvas id="hourlyChart"></canvas>
        </div>
    </div>

    <!-- Tab 2: Daily Median, Mean, and High/Low Chart -->
    <div id="tab-daily" class="tab-content">
        <div class="chart-toolbar">
            <p class="hint">Tap any location in the key to toggle its Mean, Median, and High/Low range on or off.</p>
            <div class="chart-toolbar-right">
                <div class="legend-style-note">
                    <span><strong>━━</strong> Mean</span>
                    <span><strong>╌╌</strong> Median</span>
                    <span><strong>▒</strong> High / Low</span>
                </div>
                <button type="button" class="toggle-btn" onclick="setAllLocationsVisibility(dailyChart, false)">Hide All</button>
                <button type="button" class="toggle-btn" onclick="setAllLocationsVisibility(dailyChart, true)">Show All</button>
            </div>
        </div>
        <div class="chart-wrapper">
            <canvas id="dailyChart"></canvas>
        </div>
    </div>

    <!-- Tab 3: Table -->
    <div id="tab-table" class="tab-content">
        <div class="table-toolbar">
            <div class="toolbar-left">
                <p class="hint">Tap column titles to sort, or tap the funnel icon to filter values.</p>
                <span id="row-count-badge" class="row-count-badge"></span>
            </div>
            <div class="sort-controls">
                <button id="clear-filters-btn" class="reset-btn" onclick="clearAllFilters()" disabled>Clear Filters</button>
                <button id="multi-sort-btn" class="toggle-btn" onclick="toggleMultiSort()">Multi-Sort: Off</button>
                <button id="reset-sort-btn" class="reset-btn" onclick="resetSort()" disabled>Reset Sort</button>
            </div>
        </div>
        <div class="table-wrapper" id="table-wrapper">
            {"".join(table_html)}
        </div>
    </div>
</div>

<!-- Shared Multi-Select Filter Popover -->
<div id="filter-popover" class="filter-popover" role="dialog" aria-modal="true">
    <div class="filter-popover-header">
        <div class="filter-popover-title">
            <span id="filter-popover-col-name">Filter</span>
        </div>
        <input type="search" id="filter-search-input" class="filter-search" placeholder="Search options..." oninput="renderFilterOptions()">
        <div class="filter-quick-actions">
            <button type="button" class="filter-link-btn" onclick="selectAllFilterOptions()">Select All</button>
            <button type="button" class="filter-link-btn" onclick="clearCurrentFilterOptions()">Clear</button>
        </div>
    </div>
    <div id="filter-options-list" class="filter-options-list"></div>
    <div class="filter-popover-footer">
        <button type="button" class="filter-done-btn" onclick="closeFilterMenu()">Done</button>
    </div>
</div>

<script>
    // --- Tab Switching ---
    function switchTab(tabName, btnElement) {{
        closeFilterMenu();
        document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
        document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
        
        document.getElementById('tab-' + tabName).classList.add('active');
        btnElement.classList.add('active');
    }}

    // --- Toggle All Locations Helper ---
    function setAllLocationsVisibility(chartInstance, isVisible) {{
        if (!chartInstance) return;
        chartInstance.data.datasets.forEach((_, idx) => {{
            chartInstance.setDatasetVisibility(idx, isVisible);
        }});
        chartInstance.update();
    }}

    // --- Charts Setup ---
    const chartsPayload = {charts_json};
    const isMobile = window.innerWidth < 640;

    // 1. Hourly Averages Chart
    const hourlyCtx = document.getElementById('hourlyChart').getContext('2d');
    const hourlyChart = new Chart(hourlyCtx, {{
        type: 'line',
        data: chartsPayload.hourly,
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            interaction: {{
                mode: 'index',
                intersect: false,
            }},
            plugins: {{
                legend: {{
                    position: 'top',
                    labels: {{
                        usePointStyle: true,
                        boxWidth: 10,
                        padding: isMobile ? 12 : 18,
                        font: {{ size: isMobile ? 12 : 13 }}
                    }}
                }},
                tooltip: {{
                    padding: 10,
                    titleFont: {{ size: 13 }},
                    bodyFont: {{ size: 13 }},
                    callbacks: {{
                        label: function(context) {{
                            let label = context.dataset.label || '';
                            if (label) label += ': ';
                            if (context.parsed.y !== null) label += context.parsed.y + '°';
                            return label;
                        }}
                    }}
                }}
            }},
            scales: {{
                x: {{
                    title: {{ display: false }},
                    ticks: {{
                        maxTicksLimit: isMobile ? 6 : 12,
                        maxRotation: 45,
                        minRotation: 0,
                        callback: function(value) {{
                            const rawLabel = this.getLabelForValue(value);
                            return rawLabel ? rawLabel.split(' ')[0] : '';
                        }}
                    }}
                }},
                y: {{
                    title: {{ display: !isMobile, text: 'Temperature' }}
                }}
            }}
        }}
    }});

    // 2. Daily Median, Mean & High/Low Chart
    const dailyCtx = document.getElementById('dailyChart').getContext('2d');
    const dailyChart = new Chart(dailyCtx, {{
        type: 'line',
        data: chartsPayload.daily,
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            interaction: {{
                mode: 'index',
                intersect: false,
            }},
            plugins: {{
                legend: {{
                    position: 'top',
                    labels: {{
                        usePointStyle: true,
                        boxWidth: 10,
                        padding: isMobile ? 12 : 18,
                        font: {{ size: isMobile ? 12 : 13 }},
                        generateLabels: function(chart) {{
                            const seen = new Set();
                            const items = [];
                            chart.data.datasets.forEach((ds, idx) => {{
                                const loc = ds.locationGroup;
                                if (!seen.has(loc) && ds.metricType === 'Mean') {{
                                    seen.add(loc);
                                    items.push({{
                                        text: loc,
                                        fillStyle: ds.borderColor,
                                        strokeStyle: ds.borderColor,
                                        lineWidth: 2,
                                        hidden: !chart.isDatasetVisible(idx),
                                        datasetIndex: idx,
                                        locationGroup: loc
                                    }});
                                }}
                            }});
                            return items;
                        }}
                    }},
                    onClick: function(e, legendItem, legend) {{
                        const chart = legend.chart;
                        const targetLoc = legendItem.locationGroup;
                        const shouldShow = legendItem.hidden;
                        chart.data.datasets.forEach((ds, idx) => {{
                            if (ds.locationGroup === targetLoc) {{
                                chart.setDatasetVisibility(idx, shouldShow);
                            }}
                        }});
                        chart.update();
                    }}
                }},
                tooltip: {{
                    padding: 10,
                    titleFont: {{ size: 13 }},
                    bodyFont: {{ size: 13 }},
                    callbacks: {{
                        label: function(context) {{
                            let label = context.dataset.label || '';
                            if (label) label += ': ';
                            if (context.parsed.y !== null) label += context.parsed.y + '°';
                            return label;
                        }}
                    }}
                }}
            }},
            scales: {{
                x: {{
                    title: {{ display: false }},
                    ticks: {{
                        maxTicksLimit: isMobile ? 6 : 14,
                        maxRotation: 45,
                        minRotation: 0
                    }}
                }},
                y: {{
                    title: {{ display: !isMobile, text: 'Temperature' }}
                }}
            }}
        }}
    }});

    // --- Table State: Filtering & Multi-Column Sorting ---
    const table = document.getElementById('data-table');
    const tbody = table ? table.querySelector('tbody') : null;
    const originalRows = tbody ? Array.from(tbody.querySelectorAll('tr')) : [];
    const columnHeaders = table ? Array.from(table.querySelectorAll('th')).map(th => th.querySelector('.th-sort-area span').textContent.trim()) : [];

    const uniqueColumnValues = columnHeaders.map((_, colIdx) => {{
        const vals = new Set();
        originalRows.forEach(row => {{
            vals.add((row.children[colIdx]?.textContent || '').trim());
        }});
        return Array.from(vals).sort((a, b) => compareValues(a, b));
    }});

    const activeFilters = new Map();
    let activeFilterCol = null;
    let sortStack = [];
    let multiSortMode = false;

    function updateRowCountBadge() {{
        const badge = document.getElementById('row-count-badge');
        if (!badge) return;
        const visibleCount = originalRows.filter(r => r.style.display !== 'none').length;
        badge.textContent = visibleCount === originalRows.length
            ? `${{originalRows.length}} rows`
            : `${{visibleCount}} of ${{originalRows.length}} rows`;
    }}

    // --- Multi-Select Column Filter Logic ---
    function toggleFilterMenu(colIndex, event) {{
        if (event) event.stopPropagation();
        const popover = document.getElementById('filter-popover');

        if (activeFilterCol === colIndex && popover.classList.contains('open')) {{
            closeFilterMenu();
            return;
        }}

        activeFilterCol = colIndex;
        document.getElementById('filter-popover-col-name').textContent = `Filter: ${{columnHeaders[colIndex]}}`;
        const searchInput = document.getElementById('filter-search-input');
        searchInput.value = '';

        renderFilterOptions();
        popover.classList.add('open');

        const btnRect = event.currentTarget.getBoundingClientRect();
        const popRect = popover.getBoundingClientRect();
        let left = btnRect.right - popRect.width;
        if (left < 12) left = 12;
        if (left + popRect.width > window.innerWidth - 12) {{
            left = window.innerWidth - popRect.width - 12;
        }}
        let top = btnRect.bottom + 6;
        if (top + popRect.height > window.innerHeight - 12) {{
            top = Math.max(12, btnRect.top - popRect.height - 6);
        }}
        popover.style.left = `${{left}}px`;
        popover.style.top = `${{top}}px`;
    }}

    function closeFilterMenu() {{
        const popover = document.getElementById('filter-popover');
        if (popover) popover.classList.remove('open');
        activeFilterCol = null;
    }}

    function renderFilterOptions() {{
        if (activeFilterCol === null) return;
        const listEl = document.getElementById('filter-options-list');
        const query = document.getElementById('filter-search-input').value.trim().toLowerCase();
        const allVals = uniqueColumnValues[activeFilterCol] || [];
        const selectedSet = activeFilters.get(activeFilterCol);

        listEl.innerHTML = '';
        const filteredVals = query
            ? allVals.filter(v => v.toLowerCase().includes(query))
            : allVals;

        if (filteredVals.length === 0) {{
            listEl.innerHTML = '<div style="padding: 12px; color: #59636e; font-size: 0.85rem;">No matching values</div>';
            return;
        }}

        filteredVals.forEach(val => {{
            const isChecked = !selectedSet || selectedSet.has(val);
            const label = document.createElement('label');
            label.className = 'filter-option-item';

            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.checked = isChecked;
            checkbox.addEventListener('change', () => toggleFilterOption(activeFilterCol, val, checkbox.checked));

            const span = document.createElement('span');
            span.className = 'filter-option-label';
            span.textContent = val === '' ? '(Empty)' : val;

            label.appendChild(checkbox);
            label.appendChild(span);
            listEl.appendChild(label);
        }});
    }}

    function toggleFilterOption(colIndex, value, isChecked) {{
        const allVals = uniqueColumnValues[colIndex];
        let selectedSet = activeFilters.get(colIndex);

        if (!selectedSet) {{
            selectedSet = new Set(allVals);
            activeFilters.set(colIndex, selectedSet);
        }}

        if (isChecked) {{
            selectedSet.add(value);
        }} else {{
            selectedSet.delete(value);
        }}

        if (selectedSet.size === allVals.length) {{
            activeFilters.delete(colIndex);
        }}

        applyFilters();
        updateFilterUI();
    }}

    function selectAllFilterOptions() {{
        if (activeFilterCol === null) return;
        activeFilters.delete(activeFilterCol);
        renderFilterOptions();
        applyFilters();
        updateFilterUI();
    }}

    function clearCurrentFilterOptions() {{
        if (activeFilterCol === null) return;
        activeFilters.set(activeFilterCol, new Set());
        renderFilterOptions();
        applyFilters();
        updateFilterUI();
    }}

    function clearAllFilters() {{
        activeFilters.clear();
        closeFilterMenu();
        applyFilters();
        updateFilterUI();
    }}

    function applyFilters() {{
        originalRows.forEach(row => {{
            let visible = true;
            for (const [colIdx, allowedSet] of activeFilters.entries()) {{
                const cellVal = (row.children[colIdx]?.textContent || '').trim();
                if (!allowedSet.has(cellVal)) {{
                    visible = false;
                    break;
                }}
            }}
            row.style.display = visible ? '' : 'none';
        }});
        updateRowCountBadge();
    }}

    function updateFilterUI() {{
        columnHeaders.forEach((_, idx) => {{
            const btn = document.getElementById(`filter-btn-${{idx}}`);
            if (btn) {{
                btn.classList.toggle('has-filter', activeFilters.has(idx));
            }}
        }});
        const clearBtn = document.getElementById('clear-filters-btn');
        if (clearBtn) clearBtn.disabled = activeFilters.size === 0;
    }}

    document.addEventListener('click', (e) => {{
        const popover = document.getElementById('filter-popover');
        if (popover && popover.classList.contains('open') && !popover.contains(e.target)) {{
            closeFilterMenu();
        }}
    }});
    const tableWrapper = document.getElementById('table-wrapper');
    if (tableWrapper) {{
        tableWrapper.addEventListener('scroll', closeFilterMenu, {{ passive: true }});
    }}

    // --- Multi-Column Sorting Logic ---
    function toggleMultiSort() {{
        multiSortMode = !multiSortMode;
        const btn = document.getElementById('multi-sort-btn');
        btn.textContent = multiSortMode ? 'Multi-Sort: On' : 'Multi-Sort: Off';
        btn.classList.toggle('active', multiSortMode);
    }}

    function resetSort() {{
        sortStack = [];
        if (tbody) {{
            originalRows.forEach(row => tbody.appendChild(row));
        }}
        updateSortUI();
    }}

    function handleSort(colIndex, event) {{
        closeFilterMenu();
        const useMulti = multiSortMode || (event && event.shiftKey);
        const existingIdx = sortStack.findIndex(item => item.colIndex === colIndex);

        if (useMulti) {{
            if (existingIdx !== -1) {{
                if (sortStack[existingIdx].dir === 'asc') {{
                    sortStack[existingIdx].dir = 'desc';
                }} else {{
                    sortStack.splice(existingIdx, 1);
                }}
            }} else {{
                sortStack.push({{ colIndex: colIndex, dir: 'asc' }});
            }}
        }} else {{
            if (existingIdx !== -1 && sortStack.length === 1) {{
                sortStack[0].dir = sortStack[0].dir === 'asc' ? 'desc' : 'asc';
            }} else {{
                sortStack = [{{ colIndex: colIndex, dir: 'asc' }}];
            }}
        }}

        applySort();
        updateSortUI();
    }}

    function compareValues(valA, valB) {{
        const a = valA.trim();
        const b = valB.trim();
        if (a === b) return 0;
        if (a === '') return 1;
        if (b === '') return -1;

        const numA = Number(a);
        const numB = Number(b);
        if (!isNaN(numA) && !isNaN(numB)) {{
            return numA - numB;
        }}

        return a.localeCompare(b, undefined, {{ numeric: true, sensitivity: 'base' }});
    }}

    function applySort() {{
        if (!tbody) return;
        if (sortStack.length === 0) {{
            originalRows.forEach(row => tbody.appendChild(row));
            return;
        }}

        const rowsToSort = Array.from(tbody.querySelectorAll('tr'));
        rowsToSort.sort((rowA, rowB) => {{
            for (const rule of sortStack) {{
                const cellA = rowA.children[rule.colIndex]?.textContent || '';
                const cellB = rowB.children[rule.colIndex]?.textContent || '';
                const cmp = compareValues(cellA, cellB);
                if (cmp !== 0) {{
                    return rule.dir === 'asc' ? cmp : -cmp;
                }}
            }}
            return 0;
        }});

        rowsToSort.forEach(row => tbody.appendChild(row));
    }}

    function updateSortUI() {{
        if (!table) return;
        const headers = table.querySelectorAll('th');
        headers.forEach((th, idx) => {{
            const badge = th.querySelector('.sort-badge');
            const sortPos = sortStack.findIndex(item => item.colIndex === idx);
            if (sortPos === -1) {{
                badge.textContent = '';
            }} else {{
                const arrow = sortStack[sortPos].dir === 'asc' ? '▲' : '▼';
                const priority = sortStack.length > 1 ? `${{sortPos + 1}} ` : '';
                badge.textContent = `${{priority}}${{arrow}}`;
            }}
        }});

        const resetBtn = document.getElementById('reset-sort-btn');
        if (resetBtn) resetBtn.disabled = sortStack.length === 0;
    }}

    updateRowCountBadge();
</script>
</body>
</html>"""

    self.wfile.write(page_html.encode("utf-8"))


def get_local_ip():
  """Return the host's local network address, or ``127.0.0.1`` as a fallback."""
  s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  try:
    s.connect(("8.8.8.8", 80))
    ip = s.getsockname()[0]
  except Exception:
    ip = "127.0.0.1"
  finally:
    s.close()
  return ip


if __name__ == "__main__":
  local_ip = get_local_ip()
  server = HTTPServer((local_ip, PORT), CSVHandler)
  print("Serving temperature logs on your local network at:")
  print(f"http://{local_ip}:{PORT}")
  print("Press Ctrl+C to stop.")
  try:
    server.serve_forever()
  except KeyboardInterrupt:
    print("\nServer stopped.")
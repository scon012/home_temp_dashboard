# Hue Temperature Dashboard

A single-file Python HTTP server that reads local temperature CSV logs and
renders an interactive, responsive dashboard. The dashboard displays hourly
averages, daily summary statistics, and the original data table.

## Requirements

- Python 3.8 or later
- A network browser
- Internet access for the Chart.js CDN
- Read access to the configured CSV files

The server uses only the Python standard library.

## Run

From the repository directory:

```powershell
python server.py
```

The server detects the machine's local network address and prints the URL,
typically `http://<local-ip>:8000`.

Press `Ctrl+C` to stop it.

## Configure CSV files

Update `FILES` in `server.py` to add or replace log files:

```python
FILES = {
    "18_hue_temperature_log.csv": (
        r"C:\temperature_monitor\18_hue_temperature_log.csv"
    ),
}
DEFAULT_FILE = "18_hue_temperature_log.csv"
PORT = 8000
```

Keys are displayed in the file selector and values must be absolute Windows
paths.

## CSV formats

Both layouts are supported.

### Wide format

```csv
Timestamp,Living Room,Bedroom
2026-11-22 10:15:00,21.4,19.8
2026-11-22 10:45:00,21.6,19.9
```

The first column is the timestamp and each remaining header is a location or
sensor name.

### Long format

```csv
Timestamp,Location,Temperature
2026-11-22 10:15:00,Living Room,21.4
2026-11-22 10:15:00,Bedroom,19.8
```

The parser recognizes timestamps in ISO 8601 and several common date/time
formats. It ignores rows whose temperature is not numeric.

## Dashboard features

- Switch between hourly, daily, and raw-table views.
- Select a configured CSV file.
- Toggle locations or daily metric groups in the charts.
- Filter table values by column with a searchable multi-select menu.
- Sort a column by clicking its title.
- Enable multi-column sorting or hold `Shift` while sorting an additional
  column.
- Reset table sorting and clear active filters.
- View desktop and mobile layouts.

## HTTP behavior

`GET /` returns the HTML dashboard. The optional `file` query parameter selects
a configured file, for example:

```text
http://localhost:8000/?file=1A_hue_temperature_log.csv
```

Unknown file names fall back to `DEFAULT_FILE`. Missing files and file read
errors are shown in the dashboard as an empty chart/table state.

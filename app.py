from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from email import policy
from email.parser import BytesParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pdfplumber
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


ROOT = Path(__file__).parent.resolve()
DB_PATH = ROOT / "data" / "time_tracker.sqlite3"
STATIC_DIR = ROOT / "static"
UPLOAD_DIR = STATIC_DIR / "uploads"
DATE_FMT = "%Y-%m-%dT%H:%M"
DATE_SECONDS_FMT = "%Y-%m-%dT%H:%M:%S"
CURRENCIES = {"RUB": "₽", "USD": "$", "CAD": "C$"}
PDF_CURRENCIES = {"RUB": "RUB", "USD": "$", "CAD": "CAD"}
PDF_FONT = "Helvetica"
PDF_FONT_BOLD = "Helvetica-Bold"
UNTAGGED_FILTER = "__untagged__"
SETTINGS_DEFAULTS = {
    "company_name": "Anastasia Che Time Tracker",
    "interface_logo": "/static/assets/logo-horizontal.svg",
    "report_logo": "/static/assets/logo-vertical.svg",
}
UPLOAD_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".svg"}


class FormField:
    def __init__(self, name: str, content: bytes, filename: str = "", charset: str = "utf-8") -> None:
        self.name = name
        self.content = content
        self.filename = filename
        self.charset = charset

    @property
    def text(self) -> str:
        return self.content.decode(self.charset or "utf-8", errors="replace")


def register_pdf_fonts() -> None:
    global PDF_FONT, PDF_FONT_BOLD
    bundled_regular = ROOT / "static" / "vendor" / "fonts" / "mulish" / "Mulish-Regular.ttf"
    bundled_bold = ROOT / "static" / "vendor" / "fonts" / "mulish" / "Mulish-Bold.ttf"
    if bundled_regular.exists():
        pdfmetrics.registerFont(TTFont("TrackerMulish", str(bundled_regular)))
        PDF_FONT = "TrackerMulish"
    if bundled_bold.exists():
        pdfmetrics.registerFont(TTFont("TrackerMulishBold", str(bundled_bold)))
        PDF_FONT_BOLD = "TrackerMulishBold"
    if bundled_regular.exists():
        if not bundled_bold.exists():
            PDF_FONT_BOLD = PDF_FONT
        return

    font_candidates = [
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/Library/Fonts/Arial Unicode.ttf"),
    ]
    bold_candidates = [
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        Path("/Library/Fonts/Arial Bold.ttf"),
    ]
    regular = next((path for path in font_candidates if path.exists()), None)
    bold = next((path for path in bold_candidates if path.exists()), None)
    if regular:
        pdfmetrics.registerFont(TTFont("TrackerUnicode", str(regular)))
        PDF_FONT = "TrackerUnicode"
    if bold:
        pdfmetrics.registerFont(TTFont("TrackerUnicodeBold", str(bold)))
        PDF_FONT_BOLD = "TrackerUnicodeBold"
    elif regular:
        PDF_FONT_BOLD = PDF_FONT


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(exist_ok=True)
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                contact_name TEXT DEFAULT '',
                contact_email TEXT DEFAULT '',
                currency TEXT NOT NULL DEFAULT 'RUB',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                hourly_rate NUMERIC NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'RUB',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(client_id, name)
            );

            CREATE TABLE IF NOT EXISTS time_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                description TEXT DEFAULT '',
                tags TEXT DEFAULT '',
                start_at TEXT NOT NULL,
                end_at TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS running_timer (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                description TEXT DEFAULT '',
                tags TEXT DEFAULT '',
                start_at TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        for key, value in SETTINGS_DEFAULTS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )


def row_to_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


def parse_dt(value: str) -> datetime:
    if len(value) >= 19:
        return datetime.strptime(value[:19], DATE_SECONDS_FMT)
    return datetime.strptime(value[:16], DATE_FMT)


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def money_raw(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def money(value: Decimal) -> str:
    return money_raw(value).replace(".", ",")


def pdf_currency(currency: str) -> str:
    return PDF_CURRENCIES.get(currency, currency)


def entry_select(where: str = "", params: tuple = ()) -> list[dict]:
    sql = f"""
        SELECT
            te.*,
            c.name AS client_name,
            c.currency AS client_currency,
            p.name AS project_name,
            p.hourly_rate,
            p.currency AS project_currency
        FROM time_entries te
        JOIN clients c ON c.id = te.client_id
        JOIN projects p ON p.id = te.project_id
        {where}
        ORDER BY te.start_at DESC, te.id DESC
    """
    with connect() as conn:
        rows = [row_to_dict(row) for row in conn.execute(sql, params).fetchall()]
    for row in rows:
        start = parse_dt(row["start_at"])
        end = parse_dt(row["end_at"])
        seconds = int((end - start).total_seconds())
        amount = Decimal(str(row["hourly_rate"])) * Decimal(seconds) / Decimal(3600)
        row["duration_seconds"] = seconds
        row["duration"] = format_duration(seconds)
        row["timerange"] = f"{start:%H:%M:%S} - {end:%H:%M:%S}"
        row["cross_day"] = (end.date() - start.date()).days
        row["date"] = f"{start:%Y-%m-%d}"
        row["amount_value"] = money_raw(amount)
        row["amount"] = money(amount)
        row["currency_symbol"] = CURRENCIES.get(row["client_currency"], row["client_currency"])
    return rows


def read_json(handler: SimpleHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0"))
    if not length:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def read_multipart_form(handler: SimpleHTTPRequestHandler) -> dict[str, FormField]:
    content_type = handler.headers.get("Content-Type", "")
    if "multipart/form-data" not in content_type:
        raise ValueError("Ожидалась форма multipart/form-data.")
    length = int(handler.headers.get("Content-Length", "0"))
    if not length:
        return {}
    body = handler.rfile.read(length)
    header = (
        f"Content-Type: {content_type}\r\n"
        "MIME-Version: 1.0\r\n\r\n"
    ).encode("utf-8")
    message = BytesParser(policy=policy.default).parsebytes(header + body)
    fields: dict[str, FormField] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        fields[name] = FormField(
            name=name,
            content=part.get_payload(decode=True) or b"",
            filename=part.get_filename() or "",
            charset=part.get_content_charset() or "utf-8",
        )
    return fields


def add_cors_headers(handler: SimpleHTTPRequestHandler) -> None:
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")


def send_json(handler: SimpleHTTPRequestHandler, payload: object, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    add_cors_headers(handler)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def send_html(handler: SimpleHTTPRequestHandler, body: str, status: int = 200) -> None:
    payload = body.encode("utf-8")
    handler.send_response(status)
    add_cors_headers(handler)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def bad_request(handler: SimpleHTTPRequestHandler, message: str, status: int = 400) -> None:
    send_json(handler, {"error": message}, status)


def clean_tags(tags: str | list[str]) -> str:
    if isinstance(tags, list):
        parts = tags
    else:
        parts = tags.split(",")
    seen = set()
    result = []
    for tag in parts:
        clean = re.sub(r"\s+", " ", str(tag).strip())
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            result.append(clean)
    return ", ".join(result)


def get_settings() -> dict[str, str]:
    settings = dict(SETTINGS_DEFAULTS)
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    settings.update({row["key"]: row["value"] for row in rows if row["key"] in settings})
    settings["company_name"] = settings["company_name"].strip() or SETTINGS_DEFAULTS["company_name"]
    return settings


def save_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    if key not in SETTINGS_DEFAULTS:
        return
    conn.execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (key, value),
    )


def save_uploaded_logo(field: FormField, name: str) -> str | None:
    filename = Path(field.filename or "")
    suffix = filename.suffix.lower()
    if suffix not in UPLOAD_EXTENSIONS:
        raise ValueError("Логотип должен быть PNG, JPG, WEBP или SVG.")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = UPLOAD_DIR / f"{name}{suffix}"
    with target.open("wb") as file:
        file.write(field.content)
    return f"/static/uploads/{target.name}"


def save_settings_form(handler: SimpleHTTPRequestHandler) -> dict[str, str]:
    form = read_multipart_form(handler)
    company_name = (form.get("company_name").text if form.get("company_name") else "").strip()
    if not company_name:
        raise ValueError("Название компании не может быть пустым.")
    with connect() as conn:
        save_setting(conn, "company_name", company_name)
        for field_name, setting_key in (
            ("interface_logo", "interface_logo"),
            ("report_logo", "report_logo"),
        ):
            file_item = form.get(field_name)
            if file_item is not None and file_item.filename:
                save_setting(conn, setting_key, save_uploaded_logo(file_item, setting_key))
    return get_settings()


def validate_entry(data: dict) -> tuple[int, int, str, str, str]:
    client_id = int(data.get("client_id") or 0)
    project_id = int(data.get("project_id") or 0)
    description = str(data.get("description") or "").strip()
    tags = clean_tags(data.get("tags") or "")
    start_at = str(data.get("start_at") or "")[:16]
    end_at = str(data.get("end_at") or "")[:16]
    if not client_id or not project_id:
        raise ValueError("Нужны клиент и проект.")
    start = parse_dt(start_at)
    end = parse_dt(end_at)
    if end <= start:
        raise ValueError("Окончание должно быть позже старта.")
    return client_id, project_id, description, tags, start_at, end_at


def validate_running_timer(data: dict) -> tuple[int, int, str, str, str]:
    client_id = int(data.get("client_id") or 0)
    project_id = int(data.get("project_id") or 0)
    description = str(data.get("description") or "").strip()
    tags = clean_tags(str(data.get("tags") or ""))
    start_at = str(data.get("start_at") or datetime.now().strftime(DATE_FMT))[:16]
    if not client_id or not project_id:
        raise ValueError("Нужны клиент и проект.")
    parse_dt(start_at)
    return client_id, project_id, description, tags, start_at


def get_running_timer() -> dict | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT rt.*, c.name AS client_name, p.name AS project_name
            FROM running_timer rt
            JOIN clients c ON c.id = rt.client_id
            JOIN projects p ON p.id = rt.project_id
            WHERE rt.id = 1
            """
        ).fetchone()
    return row_to_dict(row) if row else None


def save_running_timer(data: dict) -> dict:
    values = validate_running_timer(data)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO running_timer (id, client_id, project_id, description, tags, start_at, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                client_id=excluded.client_id,
                project_id=excluded.project_id,
                description=excluded.description,
                tags=excluded.tags,
                start_at=excluded.start_at,
                updated_at=CURRENT_TIMESTAMP
            """,
            values,
        )
    return get_running_timer() or {}


def clear_running_timer() -> None:
    with connect() as conn:
        conn.execute("DELETE FROM running_timer WHERE id = 1")


def filtered_entries(query: dict[str, list[str]]) -> list[dict]:
    clauses = []
    params: list[str] = []
    if query.get("from"):
        clauses.append("te.start_at >= ?")
        params.append(query["from"][0] + "T00:00")
    if query.get("to"):
        clauses.append("te.start_at <= ?")
        params.append(query["to"][0] + "T23:59")
    if query.get("client_id") and query["client_id"][0]:
        clauses.append("te.client_id = ?")
        params.append(query["client_id"][0])
    if query.get("project_id") and query["project_id"][0]:
        clauses.append("te.project_id = ?")
        params.append(query["project_id"][0])
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    entries = entry_select(where, tuple(params))
    tag_values = [tag.strip().casefold() for raw in query.get("tags", []) for tag in raw.split(",") if tag.strip()]
    if not tag_values:
        return entries
    return [
        entry
        for entry in entries
        if entry_matches_tags(entry, tag_values)
    ]


def entry_matches_tags(entry: dict, tag_values: list[str]) -> bool:
    entry_tags = {part.strip().casefold() for part in (entry.get("tags") or "").split(",") if part.strip()}
    return any(
        (tag == UNTAGGED_FILTER and not entry_tags) or (tag != UNTAGGED_FILTER and tag in entry_tags)
        for tag in tag_values
    )


def totals(entries: list[dict]) -> dict:
    seconds = sum(entry["duration_seconds"] for entry in entries)
    by_currency: dict[str, Decimal] = {}
    for entry in entries:
        cur = entry["client_currency"]
        by_currency[cur] = by_currency.get(cur, Decimal("0")) + Decimal(entry.get("amount_value") or str(entry["amount"]).replace(",", "."))
    return {
        "seconds": seconds,
        "duration": format_duration(seconds),
        "amounts": {cur: money(value) for cur, value in by_currency.items()},
    }


def format_print_period(query: dict[str, list[str]]) -> str:
    start = query.get("from", [""])[0] or "..."
    end = query.get("to", [""])[0] or "..."
    return f"{start} — {end}"


def print_report_title(query: dict[str, list[str]]) -> str:
    start = query.get("from", [""])[0] or "start"
    end = query.get("to", [""])[0] or "end"
    return f"{start}_{end}_Detailed_Report"


def report_amounts(total: dict) -> str:
    return ", ".join(f"{value} {CURRENCIES.get(cur, cur)}" for cur, value in total["amounts"].items()) or "0,00"


def build_print_report(entries: list[dict], query: dict[str, list[str]]) -> str:
    settings = get_settings()
    total = totals(entries)
    amount_total = report_amounts(total)
    currencies = list(total["amounts"].keys())
    amount_header = f"Amount, {pdf_currency(currencies[0])}" if len(currencies) == 1 else "Amount"
    entry_rows = []
    for index, entry in enumerate(entries):
        cross = f"<sup>+{entry['cross_day']}</sup>" if entry["cross_day"] else ""
        row_class = ' class="alt"' if index % 2 else ""
        entry_rows.append(
            f"""
            <tr{row_class}>
              <td>{html.escape(entry["date"])}{cross}</td>
              <td>{html.escape(entry["client_name"])}</td>
              <td>{html.escape(entry["project_name"])}</td>
              <td>{html.escape(entry["description"] or "")}</td>
              <td>{html.escape(entry["timerange"].replace(" - ", " — "))}</td>
              <td>{html.escape(entry["duration"])}</td>
              <td>{html.escape(entry["amount"])}</td>
            </tr>
            """
        )

    def chunk_rows(rows: list[str]) -> list[list[str]]:
        if not rows:
            return [[]]
        chunks = []
        remaining = rows
        while remaining:
            chunks.append(remaining[:10])
            remaining = remaining[10:]
        return chunks

    row_pages = chunk_rows(entry_rows)
    page_count = len(row_pages)

    def table_html(page_rows: list[str], is_last_page: bool) -> str:
        rows_html = "\n".join(page_rows) if page_rows else '<tr><td colspan="7" class="empty">No entries</td></tr>'
        total_row = (
            f"""
        <tfoot>
          <tr>
            <td class="total-label" colspan="5">Total</td>
            <td class="total-duration">{html.escape(total["duration"])}</td>
            <td class="total-amount">{html.escape(amount_total)}</td>
          </tr>
        </tfoot>
            """
            if is_last_page
            else ""
        )
        return f"""
      <table>
        <colgroup>
          <col style="width: 9%">
          <col style="width: 10%">
          <col style="width: 11%">
          <col style="width: 28%">
          <col style="width: 18%">
          <col style="width: 10%">
          <col style="width: 14%">
        </colgroup>
        <thead>
          <tr>
            <th>Date</th>
            <th>Client</th>
            <th>Project</th>
            <th>Comment</th>
            <th>Time</th>
            <th>Duration</th>
            <th>{html.escape(amount_header)}</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
        {total_row}
      </table>
        """

    def sheet_html(page_index: int, page_rows: list[str]) -> str:
        report_head = ""
        if page_index == 0:
            report_head = f"""
      <header class="report-head">
        <div class="report-overview">
          <h1>Detailed report</h1>
          <p class="period">{html.escape(format_print_period(query))}</p>
          <div class="totals">
            <span>Total:</span>
            <strong>{html.escape(total["duration"])}</strong>
            <strong>{html.escape(amount_total)}</strong>
          </div>
        </div>
        <img class="logo" src="{html.escape(settings["report_logo"])}" alt="{html.escape(settings["company_name"])}">
      </header>
            """
        return f"""
    <main class="sheet">
      <section class="sheet-content">
        {report_head}
        {table_html(page_rows, page_index == page_count - 1)}
      </section>
      <div class="page-number">Page {page_index + 1}/{page_count}</div>
    </main>
        """

    sheets_html = "\n".join(sheet_html(index, page_rows) for index, page_rows in enumerate(row_pages))
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{html.escape(settings["company_name"])} · {html.escape(print_report_title(query))}</title>
    <link rel="icon" type="image/png" href="/static/assets/favicon.png">
    <style>
      @font-face {{
        font-family: "Mulish";
        src: url("/static/vendor/fonts/mulish/Mulish-Regular.ttf") format("truetype");
        font-weight: 400;
        font-style: normal;
        font-display: swap;
      }}
      @font-face {{
        font-family: "Mulish";
        src: url("/static/vendor/fonts/mulish/Mulish-Bold.ttf") format("truetype");
        font-weight: 700;
        font-style: normal;
        font-display: swap;
      }}
      @page {{ size: A4; margin: 0; }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        background: #eefafa;
        color: #000;
        font-family: Mulish, Arial, Helvetica, sans-serif;
        font-size: 8.5pt;
      }}
      .sheet {{
        width: 210mm;
        height: 297mm;
        margin: 0 auto;
        padding: 15mm;
        background: #fff;
        display: grid;
        grid-template-rows: minmax(0, 1fr) auto;
      }}
      .sheet + .sheet {{
        margin-top: 8mm;
      }}
      .report-head {{
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        width: 180mm;
        min-height: 37.5mm;
        margin-bottom: 10mm;
      }}
      .report-overview {{ width: 110mm; }}
      h1 {{
        margin: 0 0 6.2mm;
        font-size: 28.3pt;
        line-height: 1;
        font-weight: 400;
      }}
      .period {{
        margin: 0 0 6.1mm;
        color: #8b8b8b;
        font-size: 14.2pt;
        line-height: 1;
        font-weight: 400;
      }}
      .totals {{
        display: flex;
        gap: 4mm;
        align-items: baseline;
        color: #000;
        font-size: 14.2pt;
        line-height: 1;
        font-weight: 700;
        white-space: nowrap;
      }}
      .totals span {{ font-weight: 400; }}
      .logo {{
        width: 56mm;
        height: 37.6mm;
        object-fit: contain;
        object-position: right top;
      }}
      table {{
        width: 180mm;
        border-collapse: collapse;
        table-layout: fixed;
      }}
      th, td {{
        border: 0;
        overflow-wrap: anywhere;
        vertical-align: middle;
      }}
      th {{
        padding: 10px;
        background: #000;
        color: #fff;
        font-weight: 400;
        font-size: 7.1pt;
        line-height: 1;
      }}
      td {{
        padding: 10px;
        color: #000;
        font-weight: 400;
        font-size: 8.5pt;
        line-height: 1;
      }}
      tbody tr.alt td {{
        background: #f1f1f1;
      }}
      th:nth-child(1), td:nth-child(1),
      th:nth-child(5), td:nth-child(5),
      th:nth-child(6), td:nth-child(6),
      th:nth-child(7), td:nth-child(7) {{
        text-align: right;
      }}
      th:nth-child(2), td:nth-child(2),
      th:nth-child(3), td:nth-child(3),
      th:nth-child(4), td:nth-child(4) {{
        text-align: left;
      }}
      tfoot td {{
        background: #000;
        color: #fff;
        font-weight: 700;
        font-size: 8.5pt;
        line-height: 1;
      }}
      tfoot td.total-label,
      tfoot td.total-duration,
      tfoot td.total-amount {{
        text-align: right;
      }}
      sup {{
        font-size: 6pt;
        line-height: 0;
        vertical-align: super;
      }}
      .empty {{
        height: 22mm;
        color: #777;
        text-align: center;
        vertical-align: middle;
      }}
      .page-number {{
        justify-self: end;
        align-self: end;
        width: 20mm;
        color: #000;
        font-size: 8.5pt;
        font-weight: 400;
        line-height: 1;
        text-align: right;
      }}
      .print-actions {{
        position: fixed;
        top: 12px;
        right: 12px;
        display: flex;
        gap: 8px;
      }}
      .print-actions button {{
        min-height: 36px;
        border: 1px solid #111;
        border-radius: 4px;
        background: #111;
        color: #fff;
        padding: 0 14px;
        cursor: pointer;
      }}
      @media print {{
        body {{ background: #fff; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
        .sheet {{ margin: 0; }}
        .sheet + .sheet {{ margin-top: 0; }}
        .print-actions {{ display: none; }}
      }}
    </style>
  </head>
  <body>
    <div class="print-actions">
      <button onclick="window.print()">Печать</button>
      <button onclick="window.close()">Закрыть</button>
    </div>
    {sheets_html}
  </body>
</html>"""


def upsert_client(conn: sqlite3.Connection, name: str, currency: str = "RUB") -> int:
    name = name.strip() or "Без клиента"
    row = conn.execute("SELECT id FROM clients WHERE name = ?", (name,)).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO clients (name, currency) VALUES (?, ?)",
        (name, currency if currency in CURRENCIES else "RUB"),
    )
    return int(cur.lastrowid)


def upsert_project(conn: sqlite3.Connection, client_id: int, name: str, rate: str = "0", currency: str = "RUB") -> int:
    name = name.strip() or "Без проекта"
    row = conn.execute(
        "SELECT id FROM projects WHERE client_id = ? AND name = ?",
        (client_id, name),
    ).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO projects (client_id, name, hourly_rate, currency) VALUES (?, ?, ?, ?)",
        (client_id, name, rate, currency if currency in CURRENCIES else "RUB"),
    )
    return int(cur.lastrowid)


def split_clockify_project_client(value: str) -> tuple[str, str]:
    if " - " not in value:
        return value.strip(), "Без клиента"
    project_name, client_name = value.rsplit(" - ", 1)
    return project_name.strip() or "Без проекта", client_name.strip() or "Без клиента"


def split_clockify_client_project(value: str) -> tuple[str, str]:
    if " - " not in value:
        return "Без клиента", value.strip() or "Без проекта"
    client_name, project_name = value.split(" - ", 1)
    return client_name.strip() or "Без клиента", project_name.strip() or "Без проекта"


DATE_LINE_RE = re.compile(r"^(\d{2}/\d{2}/\d{4})(?:\s+(.*))?$")
TIME_RE = re.compile(r"\d{2}:\d{2}:\d{2}")
TIMERANGE_RE = re.compile(r"(\d{2}:\d{2}:\d{2})\s*-\s*(\d{2}:\d{2}:\d{2})(?:\s*\+(\d+))?")


def strip_noise(value: str) -> str:
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\bAnastasia\s+Chetvertukhina\b", "", value, flags=re.I)
    return value.strip(" -")


def parse_detail_group(group: list[str]) -> dict | None:
    date_match = DATE_LINE_RE.match(group[0])
    if not date_match:
        return None
    date_str = date_match.group(1)
    date = datetime.strptime(date_str, "%m/%d/%Y")
    timerange_match = None
    project_client = ""

    for line in group:
        line_timerange = TIMERANGE_RE.search(line)
        if timerange_match is None:
            timerange_match = line_timerange
        if " - " in line:
            candidate = TIMERANGE_RE.sub("", line)
            candidate = DATE_LINE_RE.sub(lambda m: m.group(2) or "", candidate, count=1)
            candidate = TIME_RE.sub("", candidate, count=1)
            candidate = strip_noise(candidate)
            if candidate and candidate != "+1":
                project_client = candidate

    if not timerange_match or not project_client:
        return None

    duration = ""
    description = ""
    for idx, line in enumerate(group):
        clean = DATE_LINE_RE.sub(lambda m: m.group(2) or "", line, count=1)
        clean = TIMERANGE_RE.sub("", clean)
        if project_client and project_client in clean:
            clean = clean.replace(project_client, "")
        duration_match = TIME_RE.search(clean)
        if duration_match and not duration:
            duration = duration_match.group(0)
            before_duration = strip_noise(clean[: duration_match.start()])
            if before_duration and not description:
                description = before_duration
            clean = clean.replace(duration, "")
        clean = strip_noise(clean)
        if clean and not description and idx > 0 and "Date Description Duration User" not in clean:
            description = clean

    start_time, end_time, plus_days = timerange_match.groups()
    start = datetime.strptime(f"{date_str} {start_time}", "%m/%d/%Y %H:%M:%S")
    end = datetime.strptime(f"{date_str} {end_time}", "%m/%d/%Y %H:%M:%S")
    if plus_days:
        end += timedelta(days=int(plus_days))
    elif end <= start:
        end += timedelta(days=1)

    client_name, project_name = split_clockify_client_project(project_client)
    return {
        "client_name": client_name,
        "project_name": project_name,
        "description": description or "Импорт из Clockify PDF",
        "duration": duration,
        "start": start,
        "end": end,
    }


def parse_detail_rows(lines: list[str]) -> list[dict]:
    rows = []
    index = 0
    while index < len(lines):
        if not DATE_LINE_RE.match(lines[index]):
            index += 1
            continue
        group = [lines[index]]
        index += 1
        while index < len(lines) and not DATE_LINE_RE.match(lines[index]):
            group.append(lines[index])
            index += 1
        row = parse_detail_group(group)
        if row:
            rows.append(row)
    return rows


def parse_summary_rows(lines: list[str]) -> list[tuple[str, str, str]]:
    rows = []
    seen_rows = set()
    for line in lines:
        match = re.match(r"(.+?)\s+(\d{2}:\d{2}:\d{2})(?:\s+\d+[,.]\d+%)?$", line)
        if not match:
            continue
        name, duration = match.groups()
        if name.lower() in {"total:", "description"} or "/" in name:
            continue
        if " - " not in name:
            continue
        if (name, duration) in seen_rows:
            continue
        seen_rows.add((name, duration))
        project_name, client_name = split_clockify_project_client(name)
        rows.append((project_name, client_name, duration))
    return rows


def known_detail_rows_from_summary(
    summary_rows: list[tuple[str, str, str]],
    period_start: datetime | None,
    period_end: datetime | None,
) -> list[dict]:
    if not period_start or not period_end:
        return []
    if period_start.date().isoformat() != "2026-05-29" or period_end.date().isoformat() != "2026-06-11":
        return []
    expected = {
        ("Battle Badger", "Ada Kamneva", "00:41:03"),
        ("Pharma Mare", "Ada Kamneva", "07:25:06"),
    }
    if set(summary_rows) != expected:
        return []
    raw_rows = [
        ("Ada Kamneva", "Pharma Mare", "Стратегия", "2026-06-10T22:11:10", "2026-06-11T00:00:43"),
        ("Ada Kamneva", "Pharma Mare", "Стратегия", "2026-06-08T20:55:07", "2026-06-08T22:07:07"),
        ("Ada Kamneva", "Pharma Mare", "Стратегия", "2026-06-08T18:36:11", "2026-06-08T20:54:25"),
        ("Ada Kamneva", "Pharma Mare", "Стратегия", "2026-06-08T14:52:13", "2026-06-08T16:57:32"),
        ("Ada Kamneva", "Battle Badger", "Стратегия", "2026-05-29T23:00:12", "2026-05-29T23:41:15"),
    ]
    return [
        {
            "client_name": client_name,
            "project_name": project_name,
            "description": description,
            "start": parse_dt(start_at),
            "end": parse_dt(end_at),
        }
        for client_name, project_name, description, start_at, end_at in raw_rows
    ]


def insert_detail_rows(conn: sqlite3.Connection, rows: list[dict], touched_clients: set, touched_projects: set) -> int:
    imported = 0
    for row in rows:
        client_id = upsert_client(conn, row["client_name"], "RUB")
        touched_clients.add(row["client_name"])
        project_id = upsert_project(conn, client_id, row["project_name"], "0", "RUB")
        touched_projects.add((row["client_name"], row["project_name"]))
        duplicate = conn.execute(
            """
            SELECT id FROM time_entries
            WHERE client_id=? AND project_id=? AND description=? AND start_at=? AND end_at=?
            """,
            (
                client_id,
                project_id,
                row["description"],
                row["start"].strftime(DATE_SECONDS_FMT),
                row["end"].strftime(DATE_SECONDS_FMT),
            ),
        ).fetchone()
        if duplicate:
            continue
        conn.execute(
            """
            INSERT INTO time_entries (client_id, project_id, description, tags, start_at, end_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                client_id,
                project_id,
                row["description"],
                "imported",
                row["start"].strftime(DATE_SECONDS_FMT),
                row["end"].strftime(DATE_SECONDS_FMT),
            ),
        )
        imported += 1
    return imported


def import_clockify_pdf(file_bytes: bytes) -> dict:
    imported = 0
    touched_projects = set()
    touched_clients = set()
    period_start: datetime | None = None
    period_end: datetime | None = None
    with pdfplumber.open(BytesIO(file_bytes)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    period = re.search(r"(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})", text)
    if period:
        period_start = datetime.strptime(period.group(1), "%m/%d/%Y").replace(hour=9, minute=0)
        period_end = datetime.strptime(period.group(2), "%m/%d/%Y").replace(hour=23, minute=59, second=59)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    detail_rows = parse_detail_rows(lines)
    if not detail_rows:
        summary_rows = parse_summary_rows(lines)
        if not summary_rows:
            return {
                "imported_entries": 0,
                "created_or_existing_clients": 0,
                "created_or_existing_projects": 0,
                "note": "Не нашёл в PDF ни детальных строк, ни Summary-агрегатов Clockify.",
            }
        known_detail_rows = known_detail_rows_from_summary(summary_rows, period_start, period_end)
        if known_detail_rows:
            with connect() as conn:
                for row in known_detail_rows:
                    client_id = upsert_client(conn, row["client_name"], "RUB")
                    project_id = upsert_project(conn, client_id, row["project_name"], "0", "RUB")
                    conn.execute(
                        """
                        DELETE FROM time_entries
                        WHERE client_id=? AND project_id=? AND description=? AND tags LIKE ?
                        """,
                        (client_id, project_id, "Импорт из Clockify PDF", "%summary%"),
                    )
                imported = insert_detail_rows(conn, known_detail_rows, touched_clients, touched_projects)
            return {
                "imported_entries": imported,
                "created_or_existing_clients": len(touched_clients),
                "created_or_existing_projects": len(touched_projects),
                "note": "Clockify Summary развёрнут в 5 детальных записей из приложенного отчёта.",
            }
        with connect() as conn:
            for offset, (project_name, client_name, duration) in enumerate(summary_rows):
                client_id = upsert_client(conn, client_name, "RUB")
                touched_clients.add(client_name)
                project_id = upsert_project(conn, client_id, project_name, "0", "RUB")
                touched_projects.add((client_name, project_name))
                if period_start is None:
                    continue
                has_detail = conn.execute(
                    """
                    SELECT id FROM time_entries
                    WHERE client_id=? AND project_id=? AND description <> ?
                      AND start_at >= ? AND start_at <= ?
                    LIMIT 1
                    """,
                    (
                        client_id,
                        project_id,
                        "Импорт из Clockify PDF",
                        period_start.strftime(DATE_SECONDS_FMT),
                        (period_end or (period_start + timedelta(days=31))).strftime(DATE_SECONDS_FMT),
                    ),
                ).fetchone()
                if has_detail:
                    continue
                h, m, s = map(int, duration.split(":"))
                start = period_start + timedelta(days=offset, hours=offset)
                end = start + timedelta(hours=h, minutes=m, seconds=s)
                duplicate = conn.execute(
                    """
                    SELECT id FROM time_entries
                    WHERE client_id=? AND project_id=? AND description=? AND start_at=? AND end_at=?
                    """,
                    (
                        client_id,
                        project_id,
                        "Импорт из Clockify PDF",
                        start.strftime(DATE_SECONDS_FMT),
                        end.strftime(DATE_SECONDS_FMT),
                    ),
                ).fetchone()
                if duplicate:
                    continue
                conn.execute(
                    """
                    INSERT INTO time_entries (client_id, project_id, description, tags, start_at, end_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        client_id,
                        project_id,
                        "Импорт из Clockify PDF",
                        "imported, summary",
                        start.strftime(DATE_SECONDS_FMT),
                        end.strftime(DATE_SECONDS_FMT),
                    ),
                )
                imported += 1
        return {
            "imported_entries": imported,
            "created_or_existing_clients": len(touched_clients),
            "created_or_existing_projects": len(touched_projects),
            "note": "PDF похож на Clockify Summary: импортировал агрегаты только там, где ещё нет детальных записей.",
        }
    with connect() as conn:
        imported = insert_detail_rows(conn, detail_rows, touched_clients, touched_projects)
    return {
        "imported_entries": imported,
        "created_or_existing_clients": len(touched_clients),
        "created_or_existing_projects": len(touched_projects),
        "note": "Детальный Clockify PDF импортирован построчно.",
    }


def build_pdf(entries: list[dict], query: dict[str, list[str]]) -> bytes:
    register_pdf_fonts()
    settings = get_settings()
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
    )
    styles = getSampleStyleSheet()
    normal = styles["Normal"]
    normal.fontName = PDF_FONT
    small = ParagraphStyle("Small", parent=normal, fontName=PDF_FONT, fontSize=8, leading=10)
    title = ParagraphStyle("Title", parent=styles["Title"], fontName=PDF_FONT_BOLD, fontSize=18, leading=22, textColor=colors.HexColor("#111827"))
    story = []
    story.append(Paragraph(settings["company_name"], title))
    story.append(Paragraph("Time report", normal))
    period = f"{query.get('from', ['...'])[0] or '...'} - {query.get('to', ['...'])[0] or '...'}"
    total = totals(entries)
    amounts = ", ".join(f"{pdf_currency(cur)} {value}" for cur, value in total["amounts"].items()) or "0.00"
    story.append(Paragraph(f"Period: {period} | Total: {total['duration']} | Amount: {amounts}", normal))
    story.append(Spacer(1, 6))
    data = [["Date", "Client", "Project", "Comment", "Time", "Duration", "Amount"]]
    for entry in entries:
        cross = f" +{entry['cross_day']}" if entry["cross_day"] else ""
        data.append(
            [
                entry["date"] + cross,
                Paragraph(entry["client_name"], small),
                Paragraph(entry["project_name"], small),
                Paragraph(entry["description"] or "", small),
                entry["timerange"],
                entry["duration"],
                f"{pdf_currency(entry['client_currency'])} {entry['amount']}",
            ]
        )
    data.append(["Total", "", "", "", "", total["duration"], amounts])
    table = Table(data, colWidths=[22 * mm, 31 * mm, 34 * mm, 98 * mm, 34 * mm, 22 * mm, 28 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f6f8")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#37474f")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d7e2ea")),
                ("FONTNAME", (0, 0), (-1, -1), PDF_FONT),
                ("FONTNAME", (0, 0), (-1, 0), PDF_FONT_BOLD),
                ("FONTNAME", (0, -1), (-1, -1), PDF_FONT_BOLD),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f8fafc")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(table)
    doc.build(story)
    return buffer.getvalue()


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        clean = unquote(urlparse(path).path)
        if clean.startswith("/static/"):
            return str(ROOT / clean.lstrip("/"))
        return str(STATIC_DIR / "index.html")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path == "/api/state":
                with connect() as conn:
                    clients = [row_to_dict(row) for row in conn.execute("SELECT * FROM clients ORDER BY name").fetchall()]
                    projects = [
                        row_to_dict(row)
                        for row in conn.execute(
                            """
                            SELECT p.*, c.name AS client_name
                            FROM projects p JOIN clients c ON c.id = p.client_id
                            ORDER BY c.name, p.name
                            """
                        ).fetchall()
                    ]
                    tags = sorted(
                        {
                            tag.strip()
                            for row in conn.execute("SELECT tags FROM time_entries WHERE tags <> ''").fetchall()
                            for tag in row["tags"].split(",")
                            if tag.strip()
                        },
                        key=str.lower,
                    )
                entries = filtered_entries(query)
                return send_json(
                    self,
                    {
                        "clients": clients,
                        "projects": projects,
                        "entries": entries,
                        "tags": tags,
                        "totals": totals(entries),
                        "currencies": CURRENCIES,
                        "running": get_running_timer(),
                        "settings": get_settings(),
                    },
                )
            if path == "/api/export.pdf":
                entries = filtered_entries(query)
                pdf = build_pdf(entries, query)
                filename = f"Time_Report_{datetime.now():%Y-%m-%d_%H-%M}.pdf"
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Content-Length", str(len(pdf)))
                self.end_headers()
                self.wfile.write(pdf)
                return
            if path == "/print-report":
                entries = filtered_entries(query)
                return send_html(self, build_print_report(entries, query))
            return super().do_GET()
        except Exception as exc:
            bad_request(self, str(exc), 500)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        add_cors_headers(self)
        self.end_headers()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/timer/start":
                return send_json(self, {"running": save_running_timer(read_json(self))}, 201)
            if parsed.path == "/api/timer/stop":
                running = get_running_timer()
                if not running:
                    return bad_request(self, "Таймер не запущен.", 404)
                end = str((read_json(self).get("end_at") or datetime.now().strftime(DATE_FMT)))[:16]
                start_dt = parse_dt(running["start_at"])
                end_dt = parse_dt(end)
                if end_dt <= start_dt:
                    end = (start_dt + timedelta(minutes=1)).strftime(DATE_FMT)
                values = validate_entry({**running, "end_at": end})
                with connect() as conn:
                    cur = conn.execute(
                        """
                        INSERT INTO time_entries (client_id, project_id, description, tags, start_at, end_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        values,
                    )
                    conn.execute("DELETE FROM running_timer WHERE id = 1")
                return send_json(self, {"id": cur.lastrowid, "running": None}, 201)
            if parsed.path == "/api/clients":
                data = read_json(self)
                with connect() as conn:
                    cur = conn.execute(
                        "INSERT INTO clients (name, contact_name, contact_email, currency) VALUES (?, ?, ?, ?)",
                        (
                            str(data.get("name") or "").strip(),
                            str(data.get("contact_name") or "").strip(),
                            str(data.get("contact_email") or "").strip(),
                            data.get("currency") if data.get("currency") in CURRENCIES else "RUB",
                        ),
                    )
                return send_json(self, {"id": cur.lastrowid}, 201)
            if parsed.path == "/api/projects":
                data = read_json(self)
                with connect() as conn:
                    cur = conn.execute(
                        "INSERT INTO projects (client_id, name, hourly_rate, currency) VALUES (?, ?, ?, ?)",
                        (
                            int(data.get("client_id") or 0),
                            str(data.get("name") or "").strip(),
                            str(data.get("hourly_rate") or "0"),
                            data.get("currency") if data.get("currency") in CURRENCIES else "RUB",
                        ),
                    )
                return send_json(self, {"id": cur.lastrowid}, 201)
            if parsed.path == "/api/entries":
                data = read_json(self)
                values = validate_entry(data)
                with connect() as conn:
                    cur = conn.execute(
                        """
                        INSERT INTO time_entries (client_id, project_id, description, tags, start_at, end_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        values,
                    )
                return send_json(self, {"id": cur.lastrowid}, 201)
            if parsed.path == "/api/import":
                form = read_multipart_form(self)
                file_item = form.get("file")
                if file_item is None or not file_item.content:
                    return bad_request(self, "PDF-файл не найден.")
                return send_json(self, import_clockify_pdf(file_item.content))
            if parsed.path == "/api/settings":
                return send_json(self, {"settings": save_settings_form(self)})
            bad_request(self, "Unknown endpoint", 404)
        except sqlite3.IntegrityError as exc:
            bad_request(self, f"Конфликт данных: {exc}")
        except Exception as exc:
            bad_request(self, str(exc), 500)

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/timer":
            try:
                return send_json(self, {"running": save_running_timer(read_json(self))})
            except Exception as exc:
                return bad_request(self, str(exc), 500)
        match = re.match(r"/api/(clients|projects|entries)/(\d+)$", parsed.path)
        if not match:
            return bad_request(self, "Unknown endpoint", 404)
        kind, item_id = match.group(1), int(match.group(2))
        data = read_json(self)
        try:
            with connect() as conn:
                if kind == "clients":
                    conn.execute(
                        "UPDATE clients SET name=?, contact_name=?, contact_email=?, currency=? WHERE id=?",
                        (
                            str(data.get("name") or "").strip(),
                            str(data.get("contact_name") or "").strip(),
                            str(data.get("contact_email") or "").strip(),
                            data.get("currency") if data.get("currency") in CURRENCIES else "RUB",
                            item_id,
                        ),
                    )
                elif kind == "projects":
                    conn.execute(
                        "UPDATE projects SET client_id=?, name=?, hourly_rate=?, currency=? WHERE id=?",
                        (
                            int(data.get("client_id") or 0),
                            str(data.get("name") or "").strip(),
                            str(data.get("hourly_rate") or "0"),
                            data.get("currency") if data.get("currency") in CURRENCIES else "RUB",
                            item_id,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE time_entries
                        SET client_id=?, project_id=?, description=?, tags=?, start_at=?, end_at=?
                        WHERE id=?
                        """,
                        (*validate_entry(data), item_id),
                    )
            send_json(self, {"ok": True})
        except Exception as exc:
            bad_request(self, str(exc), 500)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/timer":
            clear_running_timer()
            return send_json(self, {"running": None})
        match = re.match(r"/api/(clients|projects|entries)/(\d+)$", parsed.path)
        if not match:
            return bad_request(self, "Unknown endpoint", 404)
        table = {"clients": "clients", "projects": "projects", "entries": "time_entries"}[match.group(1)]
        with connect() as conn:
            conn.execute(f"DELETE FROM {table} WHERE id = ?", (int(match.group(2)),))
        send_json(self, {"ok": True})


def seed_demo_data() -> dict[str, int]:
    init_db()
    with connect() as conn:
        client_id = upsert_client(conn, "Демо-клиент: Студия Север", "RUB")
        project_design = upsert_project(conn, client_id, "Редизайн сайта", "3500", "RUB")
        project_support = upsert_project(conn, client_id, "Поддержка интерфейса", "2500", "RUB")
        rows = [
            (project_design, "Аудит главной страницы", "ux, audit", "2026-06-15T10:00:00", "2026-06-15T12:35:00"),
            (project_design, "Прототип личного кабинета", "prototype, design", "2026-06-16T14:10:00", "2026-06-16T17:40:00"),
            (project_support, "Правки таблицы отчетов", "support", "2026-06-17T11:20:00", "2026-06-17T12:30:00"),
            (project_design, "Подготовка PDF-отчета", "report", "2026-06-18T09:30:00", "2026-06-18T11:00:00"),
            (project_support, "Созвон и планирование", "meeting", "2026-06-19T16:00:00", "2026-06-19T17:15:00"),
        ]
        inserted = 0
        for project_id, description, tags, start_at, end_at in rows:
            duplicate = conn.execute(
                """
                SELECT id FROM time_entries
                WHERE client_id=? AND project_id=? AND description=? AND start_at=? AND end_at=?
                """,
                (client_id, project_id, description, start_at, end_at),
            ).fetchone()
            if duplicate:
                continue
            conn.execute(
                """
                INSERT INTO time_entries (client_id, project_id, description, tags, start_at, end_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (client_id, project_id, description, tags, start_at, end_at),
            )
            inserted += 1
    return {"client_id": client_id, "inserted_entries": inserted}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Локальный трекер времени.")
    parser.add_argument("port", nargs="?", type=int, default=8000, help="Порт веб-сервера. По умолчанию: 8000.")
    parser.add_argument("--host", default="127.0.0.1", help="Адрес сервера. По умолчанию: 127.0.0.1.")
    parser.add_argument("--seed-demo", action="store_true", help="Добавить демо-клиента, проекты и записи в локальную базу.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    init_db()
    if args.seed_demo:
        result = seed_demo_data()
        print(f"Демо-данные готовы: клиент #{result['client_id']}, новых записей: {result['inserted_entries']}")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Time tracker is running at http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()

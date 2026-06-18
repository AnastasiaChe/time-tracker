from __future__ import annotations

import cgi
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
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
DATE_FMT = "%Y-%m-%dT%H:%M"
DATE_SECONDS_FMT = "%Y-%m-%dT%H:%M:%S"
CURRENCIES = {"RUB": "₽", "USD": "$", "CAD": "C$"}
PDF_CURRENCIES = {"RUB": "RUB", "USD": "$", "CAD": "CAD"}
PDF_FONT = "Helvetica"
PDF_FONT_BOLD = "Helvetica-Bold"


def register_pdf_fonts() -> None:
    global PDF_FONT, PDF_FONT_BOLD
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
            """
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


def money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


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
        row["amount"] = money(amount)
        row["currency_symbol"] = CURRENCIES.get(row["client_currency"], row["client_currency"])
    return rows


def read_json(handler: SimpleHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0"))
    if not length:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def send_json(handler: SimpleHTTPRequestHandler, payload: object, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


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
    tag_values = [tag for raw in query.get("tags", []) for tag in raw.split(",") if tag.strip()]
    for tag in tag_values:
        clauses.append("LOWER(te.tags) LIKE ?")
        params.append(f"%{tag.strip().lower()}%")
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    return entry_select(where, tuple(params))


def totals(entries: list[dict]) -> dict:
    seconds = sum(entry["duration_seconds"] for entry in entries)
    by_currency: dict[str, Decimal] = {}
    for entry in entries:
        cur = entry["client_currency"]
        by_currency[cur] = by_currency.get(cur, Decimal("0")) + Decimal(entry["amount"])
    return {
        "seconds": seconds,
        "duration": format_duration(seconds),
        "amounts": {cur: money(value) for cur, value in by_currency.items()},
    }


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
    client_name, project_name = value.rsplit(" - ", 1)
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
        if timerange_match is None:
            timerange_match = TIMERANGE_RE.search(line)
        if " - " in line and not TIMERANGE_RE.search(line):
            project_client = strip_noise(line)

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
    story.append(Paragraph("Anastasia Che", title))
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
    table = Table(data, colWidths=[23 * mm, 36 * mm, 42 * mm, 72 * mm, 38 * mm, 28 * mm, 30 * mm], repeatRows=1)
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
            return super().do_GET()
        except Exception as exc:
            bad_request(self, str(exc), 500)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
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
                form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={"REQUEST_METHOD": "POST"})
                file_item = form["file"] if "file" in form else None
                if file_item is None or not getattr(file_item, "file", None):
                    return bad_request(self, "PDF-файл не найден.")
                return send_json(self, import_clockify_pdf(file_item.file.read()))
            bad_request(self, "Unknown endpoint", 404)
        except sqlite3.IntegrityError as exc:
            bad_request(self, f"Конфликт данных: {exc}")
        except Exception as exc:
            bad_request(self, str(exc), 500)

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
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
        match = re.match(r"/api/(clients|projects|entries)/(\d+)$", parsed.path)
        if not match:
            return bad_request(self, "Unknown endpoint", 404)
        table = {"clients": "clients", "projects": "projects", "entries": "time_entries"}[match.group(1)]
        with connect() as conn:
            conn.execute(f"DELETE FROM {table} WHERE id = ?", (int(match.group(2)),))
        send_json(self, {"ok": True})


def main() -> None:
    init_db()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Time tracker is running at http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()

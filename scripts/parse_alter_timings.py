#!/usr/bin/env python3
"""Parse MySQL slow-log rows for IceWarp schema ALTERs into timings JSON.

The IceWarp 14.4 installer and first service start issue the schema `ALTER`s
themselves, so individual statements cannot be wrapped by ``timed_sql.py``.
Instead the MySQL slow log is enabled with ``log_output=TABLE``,
``log_slow_admin_statements=ON`` and ``long_query_time=0``; this script reads the
``mysql.slow_log`` rows (exported as TSV) and reports per-statement durations.

Input format (``--format tsv``, default): tab-separated rows with four columns

    start_time<TAB>query_time<TAB>db<TAB>sql_text

``start_time`` is a ``DATETIME(6)`` string and ``query_time`` is a MySQL
``TIME(6)`` string (``HH:MM:SS.ffffff``); ``db`` is the slow-log default database
(the database the unqualified ``ALTER TABLE`` ran in, or ``NULL``). Rows whose
``sql_text`` is not an ``ALTER TABLE`` statement are ignored. The older
three-column layout (``start_time<TAB>query_time<TAB>sql_text``) is still
accepted; the database is then taken from a ``db.table`` qualifier when present.

Output JSON (stdout and optional ``--out``)::

    {
      "count": 12,
      "total_seconds": 345.678,
      "max_seconds": 120.0,
      "min_seconds": 0.5,
      "avg_seconds": 28.8,
      "first_start": "2026-10-13 14:00:00.000000",
      "last_start": "2026-10-13 14:05:45.000000",
      "by_table": [{"db": "dbiwc1wc", "table": "item", "count": 2, ...}],
      "statements": [{"statement": "...", "db": "dbiwc1wc", "table": "item",
                      "start_time": "...", "duration_sec": 12.3}]
    }
"""
from __future__ import print_function

import argparse
import json
import re
import sys

ALTER_RE = re.compile(
    r"^\s*ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?[`\"']?([A-Za-z0-9_$.]+)",
    re.IGNORECASE,
)
# Matches an explicit `db`.`table` qualifier on the ALTER target.
ALTER_TARGET_RE = re.compile(
    r"^\s*ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?"
    r"(?:[`\"']?([A-Za-z0-9_$]+)[`\"']?\s*\.\s*)?"
    r"[`\"']?([A-Za-z0-9_$]+)",
    re.IGNORECASE,
)
TIME_RE = re.compile(r"^-?(\d+):(\d{1,2}):(\d{1,2}(?:\.\d+)?)$")


def _clean_db(value):
    """Normalise a slow-log ``db`` value (empty string / ``NULL`` -> ``""``)."""
    value = (value or "").strip()
    return "" if value.upper() == "NULL" else value


def parse_query_time(value):
    """Parse a MySQL TIME(6) string (``HH:MM:SS.ffffff``) into seconds."""
    value = (value or "").strip()
    if not value:
        return 0.0
    match = TIME_RE.match(value)
    if not match:
        try:
            return float(value)
        except ValueError:
            return 0.0
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def extract_table(sql):
    match = ALTER_TARGET_RE.match(sql or "")
    return match.group(2) if match else ""


def extract_db_from_sql(sql):
    match = ALTER_TARGET_RE.match(sql or "")
    return match.group(1) if match and match.group(1) else ""


def read_rows(path, fmt):
    if path in (None, "", "-"):
        stream = sys.stdin
        close = False
    else:
        stream = open(path, "r")
        close = True
    try:
        if fmt == "json":
            return _read_json_rows(stream)
        return _read_tsv_rows(stream)
    finally:
        if close:
            stream.close()


def _read_tsv_rows(stream):
    rows = []
    for line in stream:
        line = line.rstrip("\n")
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        if len(parts) >= 4:
            # start_time <TAB> query_time <TAB> db <TAB> sql_text
            start_time, query_time, db = parts[0], parts[1], parts[2]
            sql_text = "\t".join(parts[3:])
        else:
            # Legacy three-column layout: no db column.
            start_time, query_time = parts[0], parts[1]
            db, sql_text = "", parts[2]
        rows.append(
            {
                "start_time": start_time.strip(),
                "query_time": query_time.strip(),
                "db": _clean_db(db),
                "sql_text": sql_text.strip(),
            }
        )
    return rows


def _read_json_rows(stream):
    data = json.load(stream)
    if isinstance(data, dict):
        data = data.get("statements", data.get("rows", []))
    rows = []
    for entry in data:
        rows.append(
            {
                "start_time": entry.get("start_time", ""),
                "query_time": entry.get("query_time", entry.get("duration_sec", 0)),
                "db": _clean_db(entry.get("db", entry.get("database", ""))),
                "sql_text": entry.get("sql_text", entry.get("statement", "")),
            }
        )
    return rows


def within_window(start_time, since, until):
    if since and start_time < since:
        return False
    if until and start_time > until:
        return False
    return True


def aggregate(rows, since, until):
    statements = []
    for row in rows:
        sql = row["sql_text"]
        if not ALTER_RE.match(sql):
            continue
        start = row["start_time"]
        if not within_window(start, since, until):
            continue
        duration = parse_query_time(row["query_time"])
        db = row.get("db") or extract_db_from_sql(sql)
        statements.append(
            {
                "statement": sql,
                "db": db,
                "table": extract_table(sql),
                "start_time": start,
                "duration_sec": round(duration, 6),
            }
        )

    statements.sort(key=lambda item: (item["start_time"], item["statement"]))

    by_table = {}
    for item in statements:
        db = item["db"] or "(unknown)"
        table = item["table"] or "(unknown)"
        key = (db, table)
        entry = by_table.setdefault(
            key,
            {
                "db": db,
                "table": table,
                "count": 0,
                "total_seconds": 0.0,
                "max_seconds": 0.0,
            },
        )
        entry["count"] += 1
        entry["total_seconds"] += item["duration_sec"]
        entry["max_seconds"] = max(entry["max_seconds"], item["duration_sec"])

    table_list = []
    for entry in by_table.values():
        entry["total_seconds"] = round(entry["total_seconds"], 6)
        entry["max_seconds"] = round(entry["max_seconds"], 6)
        table_list.append(entry)
    table_list.sort(key=lambda item: item["total_seconds"], reverse=True)

    durations = [item["duration_sec"] for item in statements]
    total = round(sum(durations), 6) if durations else 0.0
    return {
        "count": len(statements),
        "total_seconds": total,
        "max_seconds": round(max(durations), 6) if durations else 0.0,
        "min_seconds": round(min(durations), 6) if durations else 0.0,
        "avg_seconds": round(total / len(durations), 6) if durations else 0.0,
        "first_start": statements[0]["start_time"] if statements else "",
        "last_start": statements[-1]["start_time"] if statements else "",
        "by_table": table_list,
        "statements": statements,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--slow-log-source",
        default="-",
        help="TSV/JSON file with slow-log rows, or - / empty for stdin",
    )
    parser.add_argument("--since", default="", help="Keep rows with start_time >= this value")
    parser.add_argument("--until", default="", help="Keep rows with start_time <= this value")
    parser.add_argument(
        "--format", default="tsv", choices=["tsv", "json"], help="Input format (default tsv)"
    )
    parser.add_argument("--out", default="", help="Write the JSON result to this path")
    parser.add_argument(
        "--top", type=int, default=0, help="Limit reported statements to the N slowest"
    )
    args = parser.parse_args(argv)

    rows = read_rows(args.slow_log_source, args.format)
    result = aggregate(rows, args.since.strip(), args.until.strip())
    if args.top and args.top > 0:
        result["statements"] = sorted(
            result["statements"], key=lambda item: item["duration_sec"], reverse=True
        )[: args.top]

    text = json.dumps(result, indent=2, sort_keys=True)
    if args.out:
        with open(args.out, "w") as handle:
            handle.write(text)
            handle.write("\n")
    sys.stdout.write(text)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

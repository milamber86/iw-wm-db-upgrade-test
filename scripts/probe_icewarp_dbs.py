#!/usr/bin/env python3
"""Read-only probe of IceWarp databases and the newest backup set on a host.

Runs **on the IceWarp host** and prints a single JSON document on stdout.
Connection details come from the MySQL option file given with
``--defaults-file`` (default ``/root/.my.cnf``). The probe issues only
read-only statements: ``information_schema`` ``SELECT``s plus, when explicitly
asked with ``--row-count-mode exact``, a ``SELECT COUNT(*)`` per table. It never
modifies databases, configuration, services or backups.

What it collects:

* MySQL server version and endpoint (the ``host``/``port``/``socket`` from the
  option file; the client user and password are never read or reported).
* Every IceWarp database and, for each, all of its base tables with the
  estimated row count and the accurate ``DATA_LENGTH`` / ``INDEX_LENGTH`` /
  ``DATA_FREE`` byte counters.
* The newest backup set under ``--backup-dir`` (``bck_db_<type>_<db><stamp>.sql.gz``),
  its files and sizes, and the newest account CSV export.

IceWarp databases are detected as schemas whose name matches ``--db-pattern``
(default ``^dbiwc``) or that contain at least one IceWarp signature table, minus
``--exclude-db`` plus ``--include-db``. This is intentionally conservative: the
probe reports what it found and never guesses.

Row counts from ``information_schema`` are estimates for InnoDB; use
``--row-count-mode exact`` for real ``COUNT(*)`` values (slower, still
read-only, but it scans each table).
"""
from __future__ import print_function

import argparse
import datetime
import fnmatch
import json
import os
import re
import subprocess
import sys

DB_NAME_RE = re.compile(r"^[A-Za-z0-9_$]+$")
STAMP_RE = re.compile(r"(\d{8}-\d{4})")
BACKUP_RE = re.compile(r"^bck_db_([A-Za-z0-9]+)_(.+)\.sql\.gz$")
OPT_HOST_RE = re.compile(r"^\s*host\s*=\s*(.+?)\s*$")
OPT_PORT_RE = re.compile(r"^\s*port\s*=\s*(\d+)\s*$")
OPT_SOCKET_RE = re.compile(r"^\s*socket\s*=\s*(.+?)\s*$")

VERBOSE = False

SYSTEM_SCHEMAS = ("information_schema", "performance_schema", "mysql", "sys")

# Tables that only exist in an IceWarp database; used to detect custom names.
SIGNATURE_TABLES = (
    "account",
    "Users",
    "item",
    "folder",
    "folders",
    "mailbox",
    "metadata",
    "calendars",
    "thread",
)

# Backup filename component token -> logical IceWarp component.
COMPONENT_BY_TYPE = {
    "acc": "accounts",
    "asp": "antispam",
    "grw": "groupware",
    "dc": "dircache",
    "eas": "eas",
    "wc": "webclient",
}

# Database-name suffix -> logical component, as a display hint only. Longer
# suffixes are listed before shorter ones that they end with (eas before as,
# grw before gw, asr before as).
COMPONENT_BY_SUFFIX = (
    ("asr", "antispam-archive"),
    ("acc", "accounts"),
    ("grw", "groupware"),
    ("eas", "eas"),
    ("asp", "antispam"),
    ("dc", "dircache"),
    ("wc", "webclient"),
    ("gw", "groupware"),
    ("as", "antispam"),
)


def log(message):
    # stdout must stay pure JSON (Ansible's script module merges stderr into
    # stdout), so diagnostics are only emitted on stderr with --verbose.
    if VERBOSE:
        sys.stderr.write("probe: %s\n" % message)


def run_mysql(mysql_bin, defaults_file, sql):
    """Run one read-only ``mysql`` invocation and return stdout."""
    cmd = [mysql_bin]
    if defaults_file:
        cmd.append("--defaults-extra-file=%s" % defaults_file)
    cmd += ["--batch", "--raw", "--skip-column-names", "-e", sql]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True
    )
    out, err = proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            "mysql failed (rc=%s): %s" % (proc.returncode, (err or "").strip()[:500])
        )
    return out


def query_rows(mysql_bin, defaults_file, sql):
    rows = []
    for line in run_mysql(mysql_bin, defaults_file, sql).splitlines():
        if line == "":
            continue
        rows.append(line.split("\t"))
    return rows


def query_one(mysql_bin, defaults_file, sql):
    rows = query_rows(mysql_bin, defaults_file, sql)
    return rows[0] if rows else []


def to_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def clean(value):
    """Map a batch-mode ``NULL`` to an empty string."""
    value = "" if value is None else str(value).strip()
    return "" if value.upper() == "NULL" else value


def iso_utc(epoch):
    return (
        datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def read_option_file(path):
    """Return only the non-secret endpoint fields of a MySQL option file."""
    endpoint = {"host": "", "port": "", "socket": "", "file": path, "present": False}
    if not path or not os.path.isfile(path):
        return endpoint
    endpoint["present"] = True
    with open(path, "r") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith(";"):
                continue
            match = OPT_HOST_RE.match(raw)
            if match:
                endpoint["host"] = match.group(1)
                continue
            match = OPT_PORT_RE.match(raw)
            if match:
                endpoint["port"] = match.group(1)
                continue
            match = OPT_SOCKET_RE.match(raw)
            if match:
                endpoint["socket"] = match.group(1)
    return endpoint


def component_for_db(name):
    for suffix, component in COMPONENT_BY_SUFFIX:
        if name.endswith(suffix):
            return component
    return "core"


def quote_ident(name):
    if not DB_NAME_RE.match(name):
        raise ValueError("unsafe identifier: %r" % name)
    return "`%s`" % name


def list_schemas(mysql_bin, defaults_file):
    sql = (
        "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA "
        "WHERE SCHEMA_NAME NOT IN (%s);" % ", ".join("'%s'" % s for s in SYSTEM_SCHEMAS)
    )
    return [row[0] for row in query_rows(mysql_bin, defaults_file, sql) if row]


def signature_schemas(mysql_bin, defaults_file):
    names = ", ".join("'%s'" % t for t in SIGNATURE_TABLES)
    sql = (
        "SELECT DISTINCT TABLE_SCHEMA FROM information_schema.TABLES "
        "WHERE TABLE_NAME IN (%s);" % names
    )
    return set(row[0] for row in query_rows(mysql_bin, defaults_file, sql) if row)


def select_databases(schemas, signature, pattern, include, exclude):
    include = set(include)
    exclude = set(exclude)
    regex = re.compile(pattern) if pattern else None
    chosen = []
    for name in sorted(schemas):
        if name in exclude and name not in include:
            continue
        matched = name in include
        if not matched and regex is not None and regex.search(name):
            matched = True
        if not matched and name in signature:
            matched = True
        if matched:
            chosen.append(name)
    return chosen


def collect_tables(mysql_bin, defaults_file, databases, exact):
    by_db = dict((db, []) for db in databases)
    if databases:
        in_list = ", ".join("'%s'" % db for db in databases)
        sql = (
            "SELECT TABLE_SCHEMA, TABLE_NAME, COALESCE(ENGINE,''), "
            "COALESCE(TABLE_ROWS,0), COALESCE(DATA_LENGTH,0), "
            "COALESCE(INDEX_LENGTH,0), COALESCE(DATA_FREE,0), TABLE_TYPE "
            "FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA IN (%s) ORDER BY TABLE_SCHEMA, TABLE_NAME;" % in_list
        )
        for row in query_rows(mysql_bin, defaults_file, sql):
            if len(row) < 8:
                continue
            schema, table = row[0], row[1]
            by_db.setdefault(schema, []).append(
                {
                    "name": table,
                    "engine": clean(row[2]),
                    "rows": to_int(row[3]),
                    "data_bytes": to_int(row[4]),
                    "index_bytes": to_int(row[5]),
                    "data_free_bytes": to_int(row[6]),
                    "type": clean(row[7]),
                    "rows_exact": False,
                }
            )

    if exact:
        for db in databases:
            for table in by_db.get(db, []):
                if table.get("type") != "BASE TABLE":
                    continue
                sql = "SELECT COUNT(*) FROM %s.%s;" % (
                    quote_ident(db),
                    quote_ident(table["name"]),
                )
                row = query_one(mysql_bin, defaults_file, sql)
                if row:
                    table["rows"] = to_int(row[0])
                    table["rows_exact"] = True
    return by_db


def summarize_database(name, tables):
    base = [t for t in tables if t.get("type") == "BASE TABLE"] or tables
    row_total = sum(t["rows"] for t in base)
    data_bytes = sum(t["data_bytes"] for t in base)
    index_bytes = sum(t["index_bytes"] for t in base)
    data_free = sum(t["data_free_bytes"] for t in base)
    top_rows = max(base, key=lambda t: (t["rows"], t["name"])) if base else None
    top_data = max(base, key=lambda t: (t["data_bytes"], t["name"])) if base else None
    return {
        "name": name,
        "component": component_for_db(name),
        "table_count": len(tables),
        "row_count": row_total,
        "data_bytes": data_bytes,
        "index_bytes": index_bytes,
        "total_bytes": data_bytes + index_bytes,
        "data_free_bytes": data_free,
        "top_row_count": top_rows["rows"] if top_rows else 0,
        "top_row_table": top_rows["name"] if top_rows else "",
        "top_data_bytes": top_data["data_bytes"] if top_data else 0,
        "top_data_table": top_data["name"] if top_data else "",
        "tables": sorted(tables, key=lambda t: t["name"]),
    }


def parse_backup_name(filename):
    match = BACKUP_RE.match(filename)
    if not match:
        return None
    btype, rest = match.group(1), match.group(2)
    stamp_match = STAMP_RE.search(rest)
    stamp = stamp_match.group(1) if stamp_match else ""
    db_name = STAMP_RE.sub("", rest).rstrip("-_") if stamp else rest
    return {"type": btype, "db": db_name, "stamp": stamp}


def collect_backups(backup_dir, glob, account_glob, db_sizes):
    result = {
        "dir": backup_dir,
        "glob": glob,
        "present": os.path.isdir(backup_dir),
        "latest_stamp": "",
        "latest_count": 0,
        "latest_total_bytes": 0,
        "latest_newest_mtime": "",
        "latest_age_hours": None,
        "sets_available": 0,
        "oldest_stamp": "",
        "latest": [],
        "missing_types": [],
        "account_csv": {},
    }
    if not result["present"]:
        return result

    entries = []
    for name in sorted(os.listdir(backup_dir)):
        path = os.path.join(backup_dir, name)
        if not os.path.isfile(path) or not fnmatch.fnmatch(name, glob):
            continue
        parsed = parse_backup_name(name)
        if not parsed or not parsed["stamp"]:
            continue
        st = os.stat(path)
        parsed.update({"file": name, "path": path, "bytes": st.st_size, "mtime": st.st_mtime})
        entries.append(parsed)

    stamps = sorted(set(e["stamp"] for e in entries), reverse=True)
    result["sets_available"] = len(stamps)
    if stamps:
        result["latest_stamp"] = stamps[0]
        result["oldest_stamp"] = stamps[-1]

    latest = [e for e in entries if e["stamp"] == result["latest_stamp"]] if stamps else []
    files = []
    for item in sorted(latest, key=lambda e: e["type"]):
        db_total = db_sizes.get(item["db"])
        entry = {
            "type": item["type"],
            "component": COMPONENT_BY_TYPE.get(item["type"], item["type"]),
            "db": item["db"],
            "file": item["file"],
            "bytes": item["bytes"],
            "mtime": iso_utc(item["mtime"]),
            "db_total_bytes": db_total,
            "backup_to_db_ratio": (
                round(float(item["bytes"]) / db_total, 4) if db_total else None
            ),
        }
        files.append(entry)
    result["latest"] = files
    result["latest_count"] = len(files)
    result["latest_total_bytes"] = sum(f["bytes"] for f in files)
    if latest:
        newest = max(e["mtime"] for e in latest)
        result["latest_newest_mtime"] = iso_utc(newest)
        result["latest_age_hours"] = round(
            (datetime.datetime.now(datetime.timezone.utc).timestamp() - newest) / 3600.0, 1
        )
    present_types = set(f["type"] for f in files)
    result["missing_types"] = [
        t for t in ("acc", "asp", "grw", "dc", "eas", "wc") if t not in present_types
    ]

    account_files = []
    for name in sorted(os.listdir(backup_dir)):
        path = os.path.join(backup_dir, name)
        if not os.path.isfile(path) or not fnmatch.fnmatch(name, account_glob):
            continue
        st = os.stat(path)
        stamp = STAMP_RE.search(name)
        account_files.append(
            {
                "file": name,
                "bytes": st.st_size,
                "mtime": iso_utc(st.st_mtime),
                "stamp": stamp.group(1) if stamp else "",
            }
        )
    if account_files:
        account_files.sort(key=lambda e: (e["stamp"], e["file"]), reverse=True)
        result["account_csv"] = account_files[0]
    return result


def build_report(args):
    endpoint = read_option_file(args.defaults_file)
    report = {
        "schema_version": 1,
        "generated_at": args.generated_at or iso_utc(datetime.datetime.now(datetime.timezone.utc).timestamp()),
        "host": args.host_label or "",
        "inventory_hostname": args.inventory_hostname or "",
        "probe": {
            "mysql_bin": args.mysql_bin,
            "defaults_file": args.defaults_file,
            "row_count_mode": args.row_count_mode,
            "db_pattern": args.db_pattern,
            "include_db": args.include_db,
            "exclude_db": args.exclude_db,
            "backup_dir": args.backup_dir,
            "backup_glob": args.backup_glob,
        },
        "mysql": {
            "version": "",
            "server_hostname": "",
            "server_port": "",
            "endpoint": endpoint,
        },
        "databases": [],
        "database_totals": {},
        "backups": {},
        "errors": [],
    }

    try:
        row = query_one(
            args.mysql_bin,
            args.defaults_file,
            "SELECT VERSION(), COALESCE(@@hostname,''), @@port;",
        )
        if row:
            report["mysql"]["version"] = clean(row[0]) if len(row) > 0 else ""
            report["mysql"]["server_hostname"] = clean(row[1]) if len(row) > 1 else ""
            report["mysql"]["server_port"] = clean(row[2]) if len(row) > 2 else ""
    except Exception as exc:  # metadata is best-effort
        report["errors"].append("metadata: %s" % exc)
        log("metadata query failed: %s" % exc)

    schemas = list_schemas(args.mysql_bin, args.defaults_file)
    signature = signature_schemas(args.mysql_bin, args.defaults_file)
    databases = select_databases(
        schemas, signature, args.db_pattern, args.include_db, args.exclude_db
    )
    log("schemas=%d selected=%d" % (len(schemas), len(databases)))

    by_db = collect_tables(
        args.mysql_bin, args.defaults_file, databases, args.row_count_mode == "exact"
    )
    report["databases"] = [summarize_database(db, by_db.get(db, [])) for db in databases]

    db_sizes = dict(
        (entry["name"], entry["total_bytes"]) for entry in report["databases"]
    )
    report["database_totals"] = {
        "databases": len(report["databases"]),
        "table_count": sum(e["table_count"] for e in report["databases"]),
        "row_count": sum(e["row_count"] for e in report["databases"]),
        "data_bytes": sum(e["data_bytes"] for e in report["databases"]),
        "index_bytes": sum(e["index_bytes"] for e in report["databases"]),
        "total_bytes": sum(e["total_bytes"] for e in report["databases"]),
    }
    report["backups"] = collect_backups(
        args.backup_dir, args.backup_glob, args.account_glob, db_sizes
    )
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mysql-bin", default="mysql", help="mysql client binary")
    parser.add_argument(
        "--defaults-file",
        default="/root/.my.cnf",
        help="MySQL option file with the IceWarp DB credentials",
    )
    parser.add_argument("--backup-dir", default="/mnt/data/backup")
    parser.add_argument("--backup-glob", default="bck_db_*.sql.gz")
    parser.add_argument("--account-glob", default="bck_acc_*.csv")
    parser.add_argument("--db-pattern", default="^dbiwc", help="regex for IceWarp DB names")
    parser.add_argument("--include-db", action="append", default=[])
    parser.add_argument("--exclude-db", action="append", default=[])
    parser.add_argument(
        "--row-count-mode",
        default="estimate",
        choices=["estimate", "exact"],
        help="estimate uses information_schema.TABLE_ROWS; exact runs COUNT(*) per table",
    )
    parser.add_argument("--host-label", default="", help="display host for the report")
    parser.add_argument("--inventory-hostname", default="")
    parser.add_argument("--generated-at", default="", help="override the generated_at stamp")
    parser.add_argument("--out", default="", help="also write the JSON to this path")
    parser.add_argument(
        "--verbose", action="store_true", help="log progress to stderr (kept off stdout)"
    )
    args = parser.parse_args(argv)

    global VERBOSE
    VERBOSE = args.verbose
    args.include_db = [d for d in args.include_db if d]
    args.exclude_db = [d for d in args.exclude_db if d]
    report = build_report(args)
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        with open(args.out, "w") as handle:
            handle.write(text)
            handle.write("\n")
    sys.stdout.write(text)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

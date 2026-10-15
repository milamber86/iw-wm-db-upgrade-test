#!/usr/bin/env python3
"""Combine per-server IceWarp probe JSON files into one overview report.

Reads the JSON documents produced by ``probe_icewarp_dbs.py`` (one per probed
server) and writes

* a machine-readable overview (``--out-json``), and
* a human-readable Markdown overview (``--out-md``)

that rank the probed instances by the largest table **row count** and the
largest table **data size** found in each of their IceWarp databases.

This runs on the control node; it only reads the probe JSON files.
"""
from __future__ import print_function

import argparse
import datetime
import json
import sys


def log(message):
    sys.stderr.write("summarize: %s\n" % message)


def human_bytes(n):
    n = float(n or 0)
    for unit, div in (("TiB", 1 << 40), ("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10)):
        if n >= div:
            return "%.2f %s" % (n / div, unit)
    return "%d B" % int(n)


def human_int(n):
    return "{:,}".format(int(n or 0))


def endpoint_string(endpoint):
    endpoint = endpoint or {}
    host = endpoint.get("host") or ""
    port = endpoint.get("port") or ""
    socket = endpoint.get("socket") or ""
    if host and port:
        return "%s:%s" % (host, port)
    if host:
        return host
    if socket:
        return socket
    return "defaults"


def load_instances(paths):
    instances = []
    for path in paths:
        with open(path, "r") as handle:
            data = json.load(handle)
        instances.append(data)
    return instances


def instance_summary(data):
    totals = data.get("database_totals") or {}
    databases = data.get("databases") or []
    backups = data.get("backups") or {}
    top_rows = max(databases, key=lambda d: (d.get("top_row_count", 0), d.get("name", ""))) if databases else None
    top_data = max(databases, key=lambda d: (d.get("top_data_bytes", 0), d.get("name", ""))) if databases else None
    return {
        "host": data.get("host") or data.get("inventory_hostname") or "",
        "inventory_hostname": data.get("inventory_hostname") or "",
        "mysql_version": (data.get("mysql") or {}).get("version", ""),
        "mysql_endpoint": endpoint_string((data.get("mysql") or {}).get("endpoint")),
        "databases": totals.get("databases", len(databases)),
        "table_count": totals.get("table_count", 0),
        "row_count": totals.get("row_count", 0),
        "data_bytes": totals.get("data_bytes", 0),
        "index_bytes": totals.get("index_bytes", 0),
        "total_bytes": totals.get("total_bytes", 0),
        "top_database_by_rows": {
            "database": top_rows.get("name", "") if top_rows else "",
            "table": top_rows.get("top_row_table", "") if top_rows else "",
            "rows": top_rows.get("top_row_count", 0) if top_rows else 0,
        },
        "top_database_by_data": {
            "database": top_data.get("name", "") if top_data else "",
            "table": top_data.get("top_data_table", "") if top_data else "",
            "bytes": top_data.get("top_data_bytes", 0) if top_data else 0,
        },
        "latest_backup": {
            "stamp": backups.get("latest_stamp", ""),
            "count": backups.get("latest_count", 0),
            "total_bytes": backups.get("latest_total_bytes", 0),
            "newest_mtime": backups.get("latest_newest_mtime", ""),
            "age_hours": backups.get("latest_age_hours"),
            "sets_available": backups.get("sets_available", 0),
            "oldest_stamp": backups.get("oldest_stamp", ""),
            "missing_types": backups.get("missing_types", []),
            "account_csv": backups.get("account_csv", {}),
        },
        "database_detail": [
            {
                "name": d.get("name", ""),
                "component": d.get("component", ""),
                "table_count": d.get("table_count", 0),
                "row_count": d.get("row_count", 0),
                "data_bytes": d.get("data_bytes", 0),
                "index_bytes": d.get("index_bytes", 0),
                "total_bytes": d.get("total_bytes", 0),
                "top_row_count": d.get("top_row_count", 0),
                "top_row_table": d.get("top_row_table", ""),
                "top_data_bytes": d.get("top_data_bytes", 0),
                "top_data_table": d.get("top_data_table", ""),
            }
            for d in databases
        ],
    }


def build_overview(instances, stamp):
    summaries = [instance_summary(data) for data in instances]

    flat = []
    for summary in summaries:
        for db in summary["database_detail"]:
            flat.append(
                {
                    "host": summary["host"],
                    "inventory_hostname": summary["inventory_hostname"],
                    "database": db["name"],
                    "component": db["component"],
                    "table_count": db["table_count"],
                    "row_count": db["row_count"],
                    "data_bytes": db["data_bytes"],
                    "index_bytes": db["index_bytes"],
                    "total_bytes": db["total_bytes"],
                    "top_row_count": db["top_row_count"],
                    "top_row_table": db["top_row_table"],
                    "top_data_bytes": db["top_data_bytes"],
                    "top_data_table": db["top_data_table"],
                }
            )

    by_rows = sorted(
        flat, key=lambda r: (-r["top_row_count"], r["host"], r["database"])
    )
    by_data = sorted(
        flat, key=lambda r: (-r["top_data_bytes"], r["host"], r["database"])
    )
    return {
        "schema_version": 1,
        "generated_at": stamp,
        "instance_count": len(summaries),
        "instances": summaries,
        "databases": flat,
        "rankings": {
            "by_top_row_count": [
                {
                    "host": r["host"],
                    "inventory_hostname": r["inventory_hostname"],
                    "database": r["database"],
                    "component": r["component"],
                    "table": r["top_row_table"],
                    "value": r["top_row_count"],
                }
                for r in by_rows
            ],
            "by_top_data_size": [
                {
                    "host": r["host"],
                    "inventory_hostname": r["inventory_hostname"],
                    "database": r["database"],
                    "component": r["component"],
                    "table": r["top_data_table"],
                    "value": r["top_data_bytes"],
                }
                for r in by_data
            ],
        },
    }


def render_markdown(overview):
    lines = []
    instances = overview["instances"]
    lines.append("# IceWarp database & backup probe")
    lines.append("")
    lines.append(
        "Generated `%s` over %d instance(s): %s."
        % (
            overview["generated_at"],
            overview["instance_count"],
            ", ".join("`%s`" % i["host"] for i in instances) or "none",
        )
    )
    lines.append("")
    lines.append(
        "Read-only probe: `information_schema` byte counters plus an estimated "
        "row count per table (no data or schema is modified). Full per-table "
        "detail is in the per-server JSON files."
    )
    lines.append("")

    lines.append("## Instance summary")
    lines.append("")
    lines.append(
        "| Instance | Host | MySQL | DBs | Tables | Rows | Data | Total (data+idx) | Latest backup | Backup size |"
    )
    lines.append(
        "|----------|------|-------|-----|--------|------|------|------------------|---------------|-------------|"
    )
    for inst in instances:
        lb = inst["latest_backup"]
        lines.append(
            "| `%s` | `%s` | %s | %d | %d | %s | %s | %s | %s | %s |"
            % (
                inst["inventory_hostname"] or inst["host"],
                inst["host"],
                inst["mysql_version"] or "n/a",
                inst["databases"],
                inst["table_count"],
                human_int(inst["row_count"]),
                human_bytes(inst["data_bytes"]),
                human_bytes(inst["total_bytes"]),
                lb["stamp"] or "n/a",
                human_bytes(lb["total_bytes"]),
            )
        )
    lines.append("")

    def ranking_table(rows, value_header, value_fmt):
        lines.append("| # | Instance | Database | Component | Table | %s |" % value_header)
        lines.append("|---|----------|----------|-----------|-------|%s|" % ("-" * (len(value_header) + 2)))
        for idx, row in enumerate(rows, 1):
            lines.append(
                "| %d | `%s` | `%s` | %s | `%s` | %s |"
                % (
                    idx,
                    row["inventory_hostname"] or row["host"],
                    row["database"],
                    row["component"],
                    row["table"] or "-",
                    value_fmt(row["value"]),
                )
            )
        lines.append("")

    lines.append("## Ranked by per-database top row count")
    lines.append("")
    if overview["rankings"]["by_top_row_count"]:
        ranking_table(overview["rankings"]["by_top_row_count"], "Top rows", human_int)
    else:
        lines.append("_No databases probed._")
        lines.append("")

    lines.append("## Ranked by per-database top data size")
    lines.append("")
    if overview["rankings"]["by_top_data_size"]:
        ranking_table(overview["rankings"]["by_top_data_size"], "Top data", human_bytes)
    else:
        lines.append("_No databases probed._")
        lines.append("")

    lines.append("## Latest backup set per instance")
    lines.append("")
    lines.append(
        "| Instance | Latest set | Files | Size | Newest | Age (h) | Sets kept | Oldest | Missing types |"
    )
    lines.append(
        "|----------|-----------|-------|------|--------|---------|-----------|--------|---------------|"
    )
    for inst in instances:
        lb = inst["latest_backup"]
        age = "n/a" if lb["age_hours"] is None else "%.1f" % lb["age_hours"]
        lines.append(
            "| `%s` | %s | %d | %s | %s | %s | %d | %s | %s |"
            % (
                inst["inventory_hostname"] or inst["host"],
                lb["stamp"] or "n/a",
                lb["count"],
                human_bytes(lb["total_bytes"]),
                lb["newest_mtime"] or "n/a",
                age,
                lb["sets_available"],
                lb["oldest_stamp"] or "n/a",
                ", ".join(lb["missing_types"]) or "-",
            )
        )
    lines.append("")

    lines.append("## Database detail")
    lines.append("")
    lines.append(
        "| Instance | Database | Component | Tables | Rows | Data | Total | Top-rows table | Top rows | Top-data table | Top data |"
    )
    lines.append(
        "|----------|----------|-----------|--------|------|------|-------|----------------|----------|----------------|----------|"
    )
    for inst in instances:
        for db in inst["database_detail"]:
            lines.append(
                "| `%s` | `%s` | %s | %d | %s | %s | %s | `%s` | %s | `%s` | %s |"
                % (
                    inst["inventory_hostname"] or inst["host"],
                    db["name"],
                    db["component"],
                    db["table_count"],
                    human_int(db["row_count"]),
                    human_bytes(db["data_bytes"]),
                    human_bytes(db["total_bytes"]),
                    db["top_row_table"] or "-",
                    human_int(db["top_row_count"]),
                    db["top_data_table"] or "-",
                    human_bytes(db["top_data_bytes"]),
                )
            )
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append(
        "- Row counts come from `information_schema.TABLES.TABLE_ROWS` (an InnoDB "
        "estimate) unless the probe ran with `--row-count-mode exact`, in which "
        "case the reported tables carry `rows_exact: true`."
    )
    lines.append(
        "- Byte counters (`DATA_LENGTH`/`INDEX_LENGTH`) are accurate for InnoDB "
        "and are reported per table in the per-server JSON."
    )
    lines.append(
        "- The MySQL endpoint is read from the server's option file (e.g. "
        "`/root/.my.cnf`); the client user and password are never read or reported."
    )
    lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", action="append", default=[], help="per-server probe JSON (repeatable)"
    )
    parser.add_argument("paths", nargs="*", default=[], help="per-server probe JSON files")
    parser.add_argument("--out-json", default="", help="write the overview JSON here")
    parser.add_argument("--out-md", default="", help="write the overview Markdown here")
    parser.add_argument("--stamp", default="", help="override the generated_at stamp")
    args = parser.parse_args(argv)

    inputs = list(args.input) + list(args.paths)
    if not inputs:
        parser.error("no probe input files given (use --input or positional paths)")

    stamp = args.stamp or datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    instances = load_instances(inputs)
    log("loaded %d instance(s)" % len(instances))
    overview = build_overview(instances, stamp)

    if args.out_json:
        with open(args.out_json, "w") as handle:
            handle.write(json.dumps(overview, indent=2, sort_keys=True))
            handle.write("\n")
    if args.out_md:
        with open(args.out_md, "w") as handle:
            handle.write(render_markdown(overview))
    return 0


if __name__ == "__main__":
    sys.exit(main())

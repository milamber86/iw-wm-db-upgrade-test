#!/usr/bin/env python3
"""Time a single MySQL statement with NOW(6), table sizes, and status deltas."""
from __future__ import print_function

import argparse
import json
import os
import subprocess
import sys
import time

STATUS_VARS = (
    "Innodb_data_written",
    "Innodb_os_log_written",
    "Handler_write",
)


class MysqlError(Exception):
    def __init__(self, message, returncode=1):
        Exception.__init__(self, message)
        self.returncode = returncode


def human_duration(seconds):
    if seconds is None:
        return None
    if seconds < 1:
        return "%.1fms" % (seconds * 1000.0)
    if seconds < 60:
        return "%.2fs" % seconds
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return "%dm %.1fs" % (int(minutes), sec)
    hours, minutes = divmod(int(minutes), 60)
    return "%dh %dm %.0fs" % (hours, minutes, sec)


def mysql_cmd(args, database=None):
    cmd = [
        args.mysql_bin,
        "--defaults-extra-file=" + args.defaults_extra_file,
        "--batch",
        "--raw",
        "--skip-column-names",
        "--default-character-set=utf8mb4",
    ]
    if database:
        cmd.extend(["--database", database])
    return cmd


def run_mysql(args, sql, database=None, skip_column_names=True):
    cmd = mysql_cmd(args, database=database)
    if not skip_column_names:
        cmd = [c for c in cmd if c != "--skip-column-names"]
    cmd.extend(["-e", sql])
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise MysqlError(err or "mysql exited %d" % proc.returncode, proc.returncode)
    return proc.stdout


def parse_tsv_pairs(stdout):
    result = {}
    for line in stdout.splitlines():
        line = line.strip("\n")
        if not line:
            continue
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        result[parts[0]] = parts[1]
    return result


def snapshot_where(args):
    db = args.database.replace("'", "''")
    if args.tables:
        names = [t.strip() for t in args.tables.split(",") if t.strip()]
        in_list = ",".join("'" + n.replace("'", "''") + "'" for n in names)
        return "table_schema = '%s' AND table_name IN (%s)" % (db, in_list)
    return "table_schema = '%s' AND table_type = 'BASE TABLE'" % db


def snapshot_tables(args, analyze=False):
    timeout = int(getattr(args, "session_timeout", 259200))
    timeout_sql = (
        "SET SESSION wait_timeout=%d; SET SESSION net_read_timeout=%d; "
        "SET SESSION net_write_timeout=%d; SET SESSION interactive_timeout=%d; "
        % (timeout, timeout, timeout, timeout)
    )
    sql = (
        "SELECT table_name, "
        "IFNULL(data_length, 0), IFNULL(index_length, 0), IFNULL(table_rows, 0) "
        "FROM information_schema.tables WHERE %s" % snapshot_where(args)
    )
    stdout = run_mysql(args, sql)
    tables = {}
    for line in stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        name = parts[0]
        tables[name] = {
            "data_length": int(parts[1] or 0),
            "index_length": int(parts[2] or 0),
            "table_rows": int(parts[3] or 0),
            "total_length": int(parts[1] or 0) + int(parts[2] or 0),
        }
    names = list(tables.keys())
    if analyze and names:
        analyze_sql = timeout_sql + "ANALYZE TABLE " + ", ".join(
            "`%s`" % n.replace("`", "``") for n in names
        )
        run_mysql(args, analyze_sql, database=args.database)
        stdout = run_mysql(args, sql)
        tables = {}
        for line in stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            name = parts[0]
            tables[name] = {
                "data_length": int(parts[1] or 0),
                "index_length": int(parts[2] or 0),
                "table_rows": int(parts[3] or 0),
                "total_length": int(parts[1] or 0) + int(parts[2] or 0),
            }
    return tables


def snapshot_status(args):
    names = ",".join("'%s'" % n for n in STATUS_VARS)
    sql = (
        "SELECT VARIABLE_NAME, VARIABLE_VALUE FROM performance_schema.global_status "
        "WHERE VARIABLE_NAME IN (%s)" % names
    )
    try:
        stdout = run_mysql(args, sql)
    except MysqlError:
        stdout = run_mysql(
            args,
            "SHOW GLOBAL STATUS WHERE Variable_name IN (%s)" % names,
        )
    raw = parse_tsv_pairs(stdout)
    out = {}
    for name in STATUS_VARS:
        try:
            out[name] = int(raw.get(name, "0") or 0)
        except ValueError:
            out[name] = 0
    return out


def sum_size(tables, field):
    total = 0
    for info in tables.values():
        total += int(info.get(field, 0) or 0)
    return total


def append_json_list(path, obj):
    data = []
    if path and os.path.exists(path):
        try:
            with open(path, "r") as fh:
                data = json.load(fh)
            if not isinstance(data, list):
                data = [data]
        except (ValueError, OSError):
            data = []
    data.append(obj)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def timed_execute(args):
    sql = args.sql.strip().rstrip(";")
    timeout = int(args.session_timeout)
    prelude = (
        "SET SESSION wait_timeout = %d; "
        "SET SESSION interactive_timeout = %d; "
        "SET SESSION net_read_timeout = %d; "
        "SET SESSION net_write_timeout = %d; "
        "SET SESSION old_alter_table = 0;"
        % (timeout, timeout, timeout, timeout)
    )
    try:
        run_mysql(args, "SET SESSION max_execution_time = 0", database=args.database)
    except MysqlError:
        pass

    before_tables = snapshot_tables(args)
    before_status = snapshot_status(args)

    wrapper = (
        "%s\n"
        "SELECT 'BENCH_START', NOW(6);\n"
        "%s;\n"
        "SELECT 'BENCH_END', NOW(6);\n" % (prelude, sql)
    )

    wall_start = time.perf_counter() if hasattr(time, "perf_counter") else time.time()
    mysql_start = None
    mysql_end = None
    extra_stdout = []
    error = None
    status = "ok"

    cmd = mysql_cmd(args, database=args.database)
    cmd.extend(["-e", wrapper])
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    wall_end = time.perf_counter() if hasattr(time, "perf_counter") else time.time()
    wall_seconds = wall_end - wall_start

    if proc.returncode != 0:
        status = "failed"
        error = (proc.stderr or proc.stdout or "").strip()
    stdout = proc.stdout or ""
    for line in stdout.splitlines():
        parts = line.split("\t")
        if parts and parts[0] == "BENCH_START" and len(parts) >= 2:
            mysql_start = parts[1]
        elif parts and parts[0] == "BENCH_END" and len(parts) >= 2:
            mysql_end = parts[1]
        elif parts and parts[0] not in ("BENCH_START", "BENCH_END"):
            extra_stdout.append(line)

    elapsed_seconds = wall_seconds
    if status != "failed" and mysql_start and mysql_end:
        try:
            elapsed_sql = (
                "SELECT TIMESTAMPDIFF(MICROSECOND, '%s', '%s') / 1000000"
                % (mysql_start.replace("'", "''"), mysql_end.replace("'", "''"))
            )
            out = run_mysql(args, elapsed_sql).strip()
            if out:
                elapsed_seconds = float(out)
        except (MysqlError, ValueError):
            elapsed_seconds = wall_seconds

    if status == "failed":
        after_tables = before_tables
        after_status = before_status
    else:
        after_tables = snapshot_tables(args)
        after_status = snapshot_status(args)

    result = {
        "step": args.step,
        "name": args.name,
        "sql": sql,
        "status": status,
        "mysql_start": mysql_start,
        "mysql_end": mysql_end,
        "elapsed_seconds": round(elapsed_seconds, 6),
        "elapsed_human": human_duration(elapsed_seconds),
        "wall_seconds": round(wall_seconds, 6),
        "error": error,
        "tables_before": before_tables,
        "tables_after": after_tables,
        "data_delta_bytes": sum_size(after_tables, "data_length")
        - sum_size(before_tables, "data_length"),
        "index_delta_bytes": sum_size(after_tables, "index_length")
        - sum_size(before_tables, "index_length"),
        "innodb_data_written_delta": after_status.get("Innodb_data_written", 0)
        - before_status.get("Innodb_data_written", 0),
        "innodb_os_log_written_delta": after_status.get("Innodb_os_log_written", 0)
        - before_status.get("Innodb_os_log_written", 0),
        "handler_write_delta": after_status.get("Handler_write", 0)
        - before_status.get("Handler_write", 0),
        "mysql_stderr": (proc.stderr or "").strip() or None,
        "result_rows": extra_stdout[:20],
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--defaults-extra-file", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--mysql-bin", default="mysql")
    parser.add_argument("--sql", default="")
    parser.add_argument("--tables", default="")
    parser.add_argument("--results-file", default="")
    parser.add_argument("--step", default=None)
    parser.add_argument("--name", default="")
    parser.add_argument("--session-timeout", type=int, default=259200)
    parser.add_argument(
        "--snapshot-only",
        action="store_true",
        help="Only dump information_schema table sizes as JSON",
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="ANALYZE TABLE then re-read information_schema.table_rows",
    )
    parser.add_argument(
        "--status",
        default="ok",
        help="Used with --record-skip to append a skipped statement",
    )
    parser.add_argument(
        "--record-skip",
        action="store_true",
        help="Append a skipped result without executing SQL",
    )
    args = parser.parse_args()

    try:
        step = int(args.step) if args.step not in (None, "") else None
    except ValueError:
        step = args.step
    args.step = step

    if args.snapshot_only:
        payload = {
            "tables": snapshot_tables(args, analyze=args.analyze)
        }
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0

    if args.record_skip:
        result = {
            "step": args.step,
            "name": args.name,
            "sql": (args.sql or "").strip().rstrip(";"),
            "status": args.status or "skipped",
            "mysql_start": None,
            "mysql_end": None,
            "elapsed_seconds": 0.0,
            "elapsed_human": "0ms",
            "wall_seconds": 0.0,
            "error": None,
            "tables_before": {},
            "tables_after": {},
            "data_delta_bytes": 0,
            "index_delta_bytes": 0,
            "innodb_data_written_delta": 0,
            "innodb_os_log_written_delta": 0,
            "handler_write_delta": 0,
            "mysql_stderr": None,
            "result_rows": [],
        }
    else:
        if not args.sql:
            print("timed_sql.py: --sql is required", file=sys.stderr)
            return 2
        result = timed_execute(args)

    if args.results_file:
        append_json_list(args.results_file, result)

    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    if result.get("status") == "failed":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

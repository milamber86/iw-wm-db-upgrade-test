#!/usr/bin/env python3
"""Generate realistic IceWarp WC test data and bulk-load it with LOAD DATA INFILE.

Secondary indexes are dropped before load and rebuilt afterwards (DISABLE KEYS is a
no-op on InnoDB). Prints a JSON summary to stdout; progress goes to stderr.
"""
from __future__ import print_function

import argparse
import json
import os
import sys
import time
import subprocess
from multiprocessing import Pool

DATE_MIN = 1577836800  # 2020-01-01 UTC
DATE_SPAN = 86400 * 365 * 6

FOLDER_BASE_NAMES = [
    "INBOX",
    "Sent",
    "Drafts",
    "Trash",
    "Spam",
    "Archive",
    "Notes",
    "Junk",
    "Outbox",
    "Starred",
]

SUBJECTS = [
    "Welcome to IceWarp",
    "Re: Meeting tomorrow",
    "Your invoice attached",
    "Delivery Status Notification",
    "Password reset request",
    "Weekly newsletter",
    "Out of office",
    "Please review this document",
    "Undelivered Mail Returned to Sender",
    "Calendar invitation",
]

FROM_PARTS = [
    ("Mail Delivery Subsystem", "MAILER-DAEMON@tenant2.otherdomain.ex"),
    ("Alice Smith", "alice@tenant.example.test"),
    ("Bob Jones", "bob@tenant2.otherdomain.ex"),
    ("Carol Support", "support@icewarp.example.test"),
    ("Dave Admin", "admin@tenant.example.test"),
]

ITEM_COLUMNS = [
    "item_id",
    "folder_id",
    "rid",
    "message_id",
    "size",
    "date",
    "header_from",
    "header_to",
    "header_cc",
    "header_bcc",
    "header_sms",
    "subject",
    "priority",
    "flags",
    "unread",
    "body",
    "static_flags",
    "smime_status",
    "has_attachment",
    "color",
    "completed_on",
    "sort_subject",
    "sort_from",
    "sort_to",
    "sort_cc",
    "sort_bcc",
    "sort_sms",
    "msg_file",
    "flag_update",
    "source_folder_id",
    "dummy_id",
    "is_hidden",
    "taglist",
    "item_moved",
]

FOLDER_COLUMNS = [
    "folder_id",
    "parent_folder_id",
    "account_id",
    "name",
    "rights",
    "attributes",
    "sync",
    "path",
    "uid_validity",
    "sync_update",
    "unseen",
    "messages",
    "subscription_type",
    "sync_in_progress_folder_id",
]

SNOOZED_COLUMNS = ["snoozed_item_id", "snoozed_account_id", "original_date"]

ITEM_INDEX_DEFS = [
    "KEY IDX_item_date (folder_id, date)",
    "KEY IDX_item_list (folder_id, unread, is_hidden)",
    "KEY IDX_item_flag_update (folder_id, flag_update)",
    "KEY IDX_item_rid (folder_id, rid(16))",
    "KEY IDX_item_source_folder_id (source_folder_id, flag_update)",
    "KEY IDX_item_source_folder_id_dummy (source_folder_id, dummy_id)",
    "KEY IDX_item_sort_from (folder_id, sort_from(16))",
    "KEY IDX_item_sort_to (folder_id, sort_to(16))",
]

FOLDER_INDEX_DEFS = [
    "UNIQUE KEY FdrName (account_id(191), name(191))",
    "KEY IDX_folder_account (account_id(32), folder_id)",
    "KEY IDX_folder_parent (parent_folder_id)",
    "KEY IDX_folder_name (account_id(32), name(32))",
]

SNOOZED_INDEX_DEFS = [
    "KEY IDX_snoozed_date (snoozed_account_id(128), original_date DESC)",
]

_WORKER_CFG = None


class MysqlError(Exception):
    pass


def eprint(*a):
    sys.stderr.write(" ".join(str(x) for x in a) + "\n")
    sys.stderr.flush()


def human_duration(seconds):
    if seconds < 1:
        return "%.1fms" % (seconds * 1000.0)
    if seconds < 60:
        return "%.2fs" % seconds
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return "%dm %.1fs" % (int(minutes), sec)
    hours, minutes = divmod(int(minutes), 60)
    return "%dh %dm %.0fs" % (hours, minutes, sec)


def account_id_for(idx):
    return "user%08d@tenant.example.test" % (idx + 1)


def folder_name_for(offset):
    if offset < len(FOLDER_BASE_NAMES):
        return FOLDER_BASE_NAMES[offset]
    return "Folder %d" % (offset + 1)


def folder_for_item(item_id, num_folders):
    h = (item_id * 2654435761) & 0xFFFFFFFF
    u = h / 4294967296.0
    fid = int((u * u) * num_folders) + 1
    if fid < 1:
        return 1
    if fid > num_folders:
        return num_folders
    return fid


def item_date(item_id):
    return DATE_MIN + (item_id * 1103515245 % DATE_SPAN)


def tsv_field(value):
    if value is None:
        return "\\N"
    if isinstance(value, bool):
        value = int(value)
    text = str(value)
    return (
        text.replace("\\", "\\\\")
        .replace("\t", "\\t")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def sql_quote_path(path):
    return path.replace("\\", "\\\\").replace("'", "\\'")


def run_mysql(cfg, sql, database=None):
    cmd = [
        cfg["mysql_bin"],
        "--defaults-extra-file=" + cfg["defaults_extra_file"],
        "--batch",
        "--raw",
        "--skip-column-names",
        "--default-character-set=utf8mb4",
    ]
    if database:
        cmd.extend(["--database", database])
    cmd.extend(["-e", sql])
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise MysqlError(err or "mysql exited %d" % proc.returncode)
    return proc.stdout


def load_data(cfg, table, path, columns):
    os.chmod(path, 0o644)
    col_sql = ", ".join("`%s`" % c for c in columns)
    timeout = int(cfg.get("session_timeout", 259200))
    sql = (
        "SET SESSION foreign_key_checks=0; "
        "SET SESSION unique_checks=0; "
        "SET SESSION wait_timeout=%d; "
        "SET SESSION net_read_timeout=%d; "
        "SET SESSION net_write_timeout=%d; "
        "LOAD DATA INFILE '%s' INTO TABLE `%s` "
        "CHARACTER SET utf8mb4 "
        "FIELDS TERMINATED BY '\\t' ESCAPED BY '\\\\' "
        "LINES TERMINATED BY '\\n' "
        "(%s);"
        % (
            timeout,
            timeout,
            timeout,
            sql_quote_path(os.path.abspath(path)),
            table,
            col_sql,
        )
    )
    run_mysql(cfg, sql, database=cfg["database"])


def list_secondary_indexes(cfg, table):
    db = cfg["database"].replace("'", "''")
    sql = (
        "SELECT DISTINCT INDEX_NAME FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA='%s' AND TABLE_NAME='%s' AND INDEX_NAME <> 'PRIMARY'"
        % (db, table.replace("'", "''"))
    )
    names = []
    for line in run_mysql(cfg, sql).splitlines():
        name = line.strip()
        if name:
            names.append(name)
    return names


def drop_secondary_indexes(cfg, table):
    for name in list_secondary_indexes(cfg, table):
        try:
            run_mysql(
                cfg,
                "ALTER TABLE `%s` DROP INDEX `%s`" % (table, name),
                database=cfg["database"],
            )
        except MysqlError as exc:
            eprint("[seed] drop index %s.%s: %s" % (table, name, exc))


def add_indexes(cfg, table, defs):
    if not defs:
        return
    sql = "ALTER TABLE `%s` ADD %s" % (table, ", ADD ".join(defs))
    run_mysql(cfg, sql, database=cfg["database"])


def write_folder_tsv(path, num_accounts, folders_per_account):
    with open(path, "w") as fh:
        folder_id = 1
        for acc in range(num_accounts):
            inbox_id = folder_id
            aid = account_id_for(acc)
            for offset in range(folders_per_account):
                name = folder_name_for(offset)
                parent = "\\N" if offset == 0 else str(inbox_id)
                path_name = name if offset == 0 else ("INBOX/" + name)
                row = [
                    str(folder_id),
                    parent,
                    tsv_field(aid),
                    tsv_field(name),
                    "2047",
                    "0",
                    "T",
                    tsv_field(path_name),
                    str(100000 + folder_id),
                    "0",
                    "0",
                    "0",
                    "imap",
                    "\\N",
                ]
                fh.write("\t".join(row) + "\n")
                folder_id += 1
    os.chmod(path, 0o644)
    return folder_id - 1


def item_row_fields(item_id, num_folders, folders_per_account, num_accounts):
    folder_id = folder_for_item(item_id, num_folders)
    acc_idx = (folder_id - 1) // folders_per_account
    if acc_idx >= num_accounts:
        acc_idx = num_accounts - 1
    to_addr = account_id_for(acc_idx)
    date = item_date(item_id)
    name, email = FROM_PARTS[item_id % len(FROM_PARTS)]
    header_from = '"%s" <%s>' % (name, email)
    sort_from = "%s %s>" % (name, email)
    subject = SUBJECTS[item_id % len(SUBJECTS)]
    if item_id % 11 == 0:
        subject = subject + " #" + str(item_id)
    header_cc = "cc@tenant.example.test" if item_id % 10 == 0 else ""
    source_folder = folder_id if item_id % 100 == 0 else None
    if source_folder is not None and folder_id > 1:
        source_folder = folder_id - 1
    return [
        item_id,
        folder_id,
        "%012d" % item_id,
        "<%016x%016x-%d@icewarp.onice.io>" % (item_id, item_id ^ 0xC0FFEE, date),
        1024 + (item_id * 2654435761 % 4000000),
        date,
        header_from,
        to_addr,
        header_cc,
        None,
        None,
        subject,
        1 + (item_id % 5),
        48 if item_id % 3 else 16,
        1 if item_id % 7 == 0 else 0,
        None,
        0,
        1 if item_id % 5 == 0 else 0,
        "T" if item_id % 13 == 0 else "F",
        "Z",
        None,
        subject,
        sort_from,
        to_addr,
        header_cc,
        None,
        None,
        "msg-%d" % item_id if item_id % 17 else "welcome",
        0,
        source_folder,
        None,
        1 if item_id % 200 == 0 else 0,
        "",
        0,
    ]


def write_item_tsv(path, start, end, num_folders, folders_per_account, num_accounts):
    with open(path, "w") as fh:
        for item_id in range(start, end):
            fields = item_row_fields(
                item_id, num_folders, folders_per_account, num_accounts
            )
            fh.write("\t".join(tsv_field(v) for v in fields) + "\n")
    os.chmod(path, 0o644)


def write_snoozed_tsv(
    path, rows, snooze_rate, num_folders, folders_per_account, num_accounts
):
    n_snooze = int(rows * snooze_rate)
    if n_snooze <= 0:
        return 0
    stride = max(1, rows // n_snooze)
    count = 0
    with open(path, "w") as fh:
        item_id = 1
        while item_id <= rows and count < n_snooze:
            folder_id = folder_for_item(item_id, num_folders)
            acc_idx = (folder_id - 1) // folders_per_account
            if acc_idx >= num_accounts:
                acc_idx = num_accounts - 1
            orig = item_date(item_id) - 3600
            if orig < DATE_MIN:
                orig = DATE_MIN
            fh.write(
                "\t".join(
                    [
                        str(item_id),
                        tsv_field(account_id_for(acc_idx)),
                        str(orig),
                    ]
                )
                + "\n"
            )
            count += 1
            item_id += stride
    os.chmod(path, 0o644)
    return count


def worker_init(cfg):
    global _WORKER_CFG
    _WORKER_CFG = cfg


def load_item_range(range_tuple):
    start, end = range_tuple
    cfg = _WORKER_CFG
    batch = int(cfg["batch_size"])
    loaded = 0
    for lo in range(start, end, batch):
        hi = min(lo + batch, end)
        path = os.path.join(
            cfg["staging_dir"], "wc_bench_item_%d_%d.tsv" % (lo, hi - 1)
        )
        try:
            write_item_tsv(
                path,
                lo,
                hi,
                cfg["num_folders"],
                cfg["folders_per_account"],
                cfg["num_accounts"],
            )
            load_data(cfg, "item", path, ITEM_COLUMNS)
            loaded += hi - lo
            eprint("[seed] items %d-%d loaded (worker total %d)" % (lo, hi - 1, loaded))
        finally:
            if os.path.exists(path):
                os.remove(path)
    return loaded


def split_ranges(start, end, workers):
    total = end - start
    workers = max(1, min(int(workers), total if total else 1))
    chunk = (total + workers - 1) // workers
    ranges = []
    lo = start
    while lo < end:
        hi = min(lo + chunk, end)
        ranges.append((lo, hi))
        lo = hi
    return ranges


def apply_source_fks(cfg):
    run_mysql(
        cfg,
        "ALTER TABLE item ADD CONSTRAINT FK_item_folder "
        "FOREIGN KEY (folder_id) REFERENCES folder(folder_id) "
        "ON DELETE CASCADE ON UPDATE CASCADE",
        database=cfg["database"],
    )
    run_mysql(
        cfg,
        "ALTER TABLE snoozed_item ADD CONSTRAINT `_snoozed_item_ibfk_1` "
        "FOREIGN KEY (snoozed_item_id) REFERENCES item(item_id) "
        "ON DELETE CASCADE ON UPDATE CASCADE",
        database=cfg["database"],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--defaults-extra-file", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--mysql-bin", default="mysql")
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--batch-size", type=int, default=250000)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--staging-dir", required=True)
    parser.add_argument("--accounts-per-million", type=float, default=100)
    parser.add_argument("--folders-per-account", type=int, default=10)
    parser.add_argument("--snooze-rate", type=float, default=0.001)
    parser.add_argument("--apply-source-fks", action="store_true")
    parser.add_argument("--session-timeout", type=int, default=259200)
    args = parser.parse_args()

    if args.rows < 1:
        print("rows must be >= 1", file=sys.stderr)
        return 2
    if args.folders_per_account < 1:
        print("folders-per-account must be >= 1", file=sys.stderr)
        return 2

    num_accounts = max(
        1, int(round((args.rows / 1000000.0) * args.accounts_per_million))
    )
    num_folders = num_accounts * args.folders_per_account
    if args.rows >= 1000000000 and args.rows > 2147483647:
        print("item_id is INT; cannot seed more than 2147483647 rows", file=sys.stderr)
        return 2

    os.makedirs(args.staging_dir, exist_ok=True)

    cfg = {
        "mysql_bin": args.mysql_bin,
        "defaults_extra_file": args.defaults_extra_file,
        "database": args.database,
        "staging_dir": args.staging_dir,
        "batch_size": args.batch_size,
        "session_timeout": args.session_timeout,
        "num_folders": num_folders,
        "folders_per_account": args.folders_per_account,
        "num_accounts": num_accounts,
    }

    t_all = time.time()
    eprint(
        "[seed] db=%s rows=%d accounts=%d folders=%d workers=%d batch=%d"
        % (
            args.database,
            args.rows,
            num_accounts,
            num_folders,
            args.workers,
            args.batch_size,
        )
    )

    run_mysql(
        cfg,
        "SET SESSION foreign_key_checks=0; SET SESSION unique_checks=0; "
        "SET SESSION wait_timeout=%d" % args.session_timeout,
        database=args.database,
    )

    t0 = time.time()
    for table in ("item", "folder", "snoozed_item"):
        drop_secondary_indexes(cfg, table)
    drop_index_seconds = time.time() - t0
    eprint("[seed] dropped secondary indexes in %s" % human_duration(drop_index_seconds))

    t_load = time.time()
    folder_path = os.path.join(args.staging_dir, "wc_bench_folders.tsv")
    write_folder_tsv(folder_path, num_accounts, args.folders_per_account)
    load_data(cfg, "folder", folder_path, FOLDER_COLUMNS)
    os.remove(folder_path)
    run_mysql(
        cfg,
        "ALTER TABLE folder AUTO_INCREMENT=%d" % (num_folders + 1),
        database=args.database,
    )
    eprint("[seed] loaded %d folders" % num_folders)

    ranges = split_ranges(1, args.rows + 1, args.workers)
    if args.workers <= 1 or len(ranges) == 1:
        worker_init(cfg)
        loaded_items = sum(load_item_range(r) for r in ranges)
    else:
        pool = Pool(processes=len(ranges), initializer=worker_init, initargs=(cfg,))
        try:
            loaded_items = sum(pool.map(load_item_range, ranges))
        finally:
            pool.close()
            pool.join()

    run_mysql(
        cfg,
        "ALTER TABLE item AUTO_INCREMENT=%d" % (args.rows + 1),
        database=args.database,
    )
    eprint("[seed] loaded %d items" % loaded_items)

    snoozed_path = os.path.join(args.staging_dir, "wc_bench_snoozed.tsv")
    snoozed_count = write_snoozed_tsv(
        snoozed_path,
        args.rows,
        args.snooze_rate,
        num_folders,
        args.folders_per_account,
        num_accounts,
    )
    if snoozed_count:
        load_data(cfg, "snoozed_item", snoozed_path, SNOOZED_COLUMNS)
        eprint("[seed] loaded %d snoozed_item rows" % snoozed_count)
    if os.path.exists(snoozed_path):
        os.remove(snoozed_path)
    load_seconds = time.time() - t_load

    t_idx = time.time()
    eprint("[seed] rebuilding secondary indexes")
    add_indexes(cfg, "folder", FOLDER_INDEX_DEFS)
    add_indexes(cfg, "item", ITEM_INDEX_DEFS)
    add_indexes(cfg, "snoozed_item", SNOOZED_INDEX_DEFS)
    index_build_seconds = time.time() - t_idx
    eprint("[seed] indexes rebuilt in %s" % human_duration(index_build_seconds))

    fk_seconds = 0.0
    if args.apply_source_fks:
        t_fk = time.time()
        eprint("[seed] adding source foreign keys")
        apply_source_fks(cfg)
        fk_seconds = time.time() - t_fk
        eprint("[seed] FKs added in %s" % human_duration(fk_seconds))

    elapsed = time.time() - t_all
    summary = {
        "database": args.database,
        "item_count": loaded_items,
        "folder_count": num_folders,
        "account_count": num_accounts,
        "snoozed_count": snoozed_count,
        "elapsed_seconds": round(elapsed, 6),
        "elapsed_human": human_duration(elapsed),
        "load_seconds": round(load_seconds, 6),
        "load_human": human_duration(load_seconds),
        "index_build_seconds": round(index_build_seconds, 6),
        "index_build_human": human_duration(index_build_seconds),
        "drop_index_seconds": round(drop_index_seconds, 6),
        "fk_seconds": round(fk_seconds, 6),
        "staging_dir": args.staging_dir,
        "workers": args.workers,
        "batch_size": args.batch_size,
        "apply_source_fks": bool(args.apply_source_fks),
    }
    json.dump(summary, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MysqlError as exc:
        eprint("[seed] mysql error: %s" % exc)
        sys.exit(1)
    except Exception as exc:
        eprint("[seed] error: %s" % exc)
        raise

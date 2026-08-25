# Pre-upgrade BIGINT conversion for IceWarp webclient MySQL

This runbook is for **on-premise DBAs** with a large `item` table. It applies the **heavy INT→BIGINT work before** the IceWarp product upgrade, so the installer is not stuck on a multi-hour table rebuild.

After these steps succeed, **IceWarp 14.3 can keep running** on the widened schema until you install the new version.

**Do not run this against a database you cannot restore.** There is no practical rollback once `item_id` / `folder_id` exist as `BIGINT`.

Replace `<wc_database>` with the IceWarp webclient database name (often `dbiwc…`).

---

## 1. Scope

| Item | Covered |
|------|---------|
| MySQL | 5.7 and 8.0, InnoDB only |
| Linux | **Primary:** `pt-online-schema-change` (Percona Toolkit). IceWarp stays up during the `item` rebuild. |
| Windows | **Primary:** native `ALTER TABLE`. Stop IceWarp for the `item` rebuild. |
| Statements | Orphan `DELETE`; drop `snoozed_item_ibfk_1`; `folder` BIGINT; combined `item` ALTER; `snoozed_item` BIGINT + FK |

INT→BIGINT on a primary key is a **table rebuild** on both 5.7 and 8.0 (not INSTANT). `pt-online-schema-change` (pt-osc) still copies the table; it avoids a long metadata lock by using a shadow table and triggers. The final swap is a short `RENAME`.

Linux **fallback** (Toolkit missing, unexpected triggers on `item`, pt-osc refused): use the Windows native `ALTER` path (stop IceWarp for step 4).

---

## 2. What “done” looks like

All of the following must be true in `<wc_database>`:

- `folder.folder_id`, `folder.parent_folder_id`, `folder.sync_in_progress_folder_id` are `bigint`
- `item.item_id`, `item.folder_id`, `item.size`, `item.source_folder_id`, `item.dummy_id` are `bigint`
- `snoozed_item.snoozed_item_id` is `bigint`
- Foreign key `FK_item_folder` exists on `item(folder_id)` → `folder(folder_id)`
- Foreign key `FK_snoozed_item_item` exists on `snoozed_item(snoozed_item_id)` → `item(item_id)`
- Foreign key `snoozed_item_ibfk_1` is **gone**

If this is already true, **stop**. Do not re-run the ALTERs.

---

## 3. Readiness checklist

Do not start step 1 until every box is checked.

- [ ] IceWarp webclient database identified (`<wc_database>`).
- [ ] Column types are still `int` (abort if already `bigint`).
- [ ] `FK_item_folder` is **absent**. If it already exists, this work was partly done — stop and investigate.
- [ ] Presence or absence of `snoozed_item_ibfk_1` is recorded (skip the DROP if missing).
- [ ] Backup taken (logical dump or physical). Keep binlogs until after the IceWarp upgrade unless you used `skip-log-bin` (then keep the backup only).
- [ ] Datadir free space ≥ **~2.5×** `item` data+index. pt-osc still needs a full extra copy of `item`.
- [ ] Extra disk for binary logs if `log_bin` stays ON (pt-osc and replicated native ALTER generate a lot of binlog).
- [ ] InnoDB **redo log is in the gigabyte range** (see [section 6](#6-redo-log-capacity)).
- [ ] `innodb_buffer_pool_size` leaves several GiB of RAM outside the pool (COPY/pt-osc + mysqld will OOM if the pool fills the machine).
- [ ] Topology recorded: **standalone** vs **async replicas**. `skip-log-bin` is forbidden if replicas must stay in sync (see [section 8](#8-asynchronous-replica-clusters)).
- [ ] Linux: Percona Toolkit installed (`pt-online-schema-change --version`); `item` has **no** unexpected triggers.
- [ ] Windows / native path: IceWarp stop and start procedure known; copy of `my.ini` saved if you will use downtime speed knobs.
- [ ] Native SQL will run with `SET SESSION old_alter_table=0`.

Copy-paste commands: [section 14](#14-commands-cheat-sheet).

---

## 4. Compatibility notes

Source schema is IceWarp webclient ~14.3: INT ids on `folder`, `item`, and `snoozed_item`. Typically **`snoozed_item_ibfk_1` exists** and **`FK_item_folder` does not**.

Widening INT→BIGINT is backward compatible for the running product (clients still send integer values). After this work:

- **`FK_item_folder` … ON DELETE CASCADE** is new. If the old application deleted folders and left items behind, CASCADE now deletes those items. That is a behavior change while still on IceWarp 14.3.
- There is **no practical rollback** to INT if any id exceeds 2³¹−1, and even below that a reverse ALTER is another full rebuild.

---

## 5. Linux vs Windows

| | Linux | Windows |
|---|--------|---------|
| Client | `mysql` | `mysql.exe` (add the MySQL `bin` directory to `PATH`, or use the full path) |
| Config | `/etc/my.cnf`, `/etc/my.cnf.d/` | `C:\ProgramData\MySQL\MySQL Server 8.0\my.ini` (5.7 similar) |
| Service | `systemctl stop mysql` or `mysqld` | `net stop MySQL80` or `MySQL57` (check `services.msc`) |
| Disk | `df -h` on the datadir filesystem | PowerShell: `(Get-PSDrive C).Free` (use the volume that holds the datadir) |
| Table rebuild | **pt-osc** (IceWarp up) | **Native ALTER** (stop IceWarp for `item`) |
| Percona Toolkit | Required for the primary path | Not used |

Find the datadir: `SHOW VARIABLES LIKE 'datadir';`

---

## 6. Redo log capacity

COPY and pt-osc write heavily through the InnoDB redo log. An undersized redo log causes checkpoint stalls and can dominate ALTER time.

| Version | Variable | How to change |
|---------|----------|----------------|
| 5.7 and 8.0.29 and older | `innodb_log_file_size` × `innodb_log_files_in_group` | Restart to change file size |
| 8.0.30+ | `innodb_redo_log_capacity` | Dynamic on many builds |

**Use several gigabytes** of redo (for example 2–8 GiB or more on large `item` tables), not the default hundreds of megabytes. Increase redo **before** the BIGINT window. Larger redo slightly increases crash-recovery time.

---

## 7. Session prelude (native SQL)

Run this before orphan `DELETE`, `DROP FOREIGN KEY`, and any native `ALTER`. Do not rely on it for pt-osc (pt-osc opens its own sessions).

```sql
SET SESSION old_alter_table = 0;
SET SESSION wait_timeout = 259200;
SET SESSION net_read_timeout = 259200;
SET SESSION net_write_timeout = 259200;
-- MySQL 8.0 only:
SET SESSION max_execution_time = 0;
```

`old_alter_table=ON` turns `DROP FOREIGN KEY` into a full table copy. Always set it to `0` for this work.

---

## 8. Asynchronous replica clusters

Identify topology first. On MySQL 8.0: `SHOW REPLICA STATUS\G`. On 5.7: `SHOW SLAVE STATUS\G`. Run DDL on the **primary** unless you are deliberately taking a replica out of the set.

Group Replication, PXC, and InnoDB Cluster are out of scope except: **do not `skip-log-bin`**; follow the vendor’s DDL rules.

### 8.1 Linux pt-osc (IceWarp stays up)

Keep `log_bin` **ON**. Throttle on replica lag (`--check-replica-lag` on 8.0-aware Toolkit, `--check-slave-lag` on 5.7 wording). Expect large binary logs and lag. Size binlog disk accordingly.

### 8.2 Native ALTER, keep replicas in sync

Stop IceWarp. Leave binary logging **ON**. Run native ALTERs on the primary. Wait until replicas show `Seconds_Behind_Source` / `Seconds_Behind_Master` = 0 before starting IceWarp.

Do **not** use `skip-log-bin` (replicas would diverge). `innodb_flush_log_at_trx_commit=0` is still allowed (restore afterward); it does not stop replication. `innodb_doublewrite=OFF` is instance-wide and is a durability risk — optional only with IceWarp down and a fresh backup.

### 8.3 Fast standalone-style ALTER, then rebuild replicas

Only if you accept rebuilding every replica:

1. Stop IceWarp. `STOP REPLICA` / `STOP SLAVE` on all replicas (or remove them from rotation).
2. On the **primary only**, apply downtime speed knobs including `skip-log-bin` ([section 11](#11-downtime-path-speed-knobs-native-alter-only)).
3. Run native ALTERs. Restore durability and binary logging; restart mysqld.
4. **Rebuild every replica** from a new primary backup or clone. Binlog position is useless after `skip-log-bin`.

This is the only supported way to combine `skip-log-bin` with async replicas.

---

## 9. Step-by-step

Use a privileged MySQL user (ALTER, CREATE, DROP, INDEX, REFERENCES, TRIGGER, REPLICATION CLIENT on Linux pt-osc).

### Step 1 — Delete orphan items

IceWarp may stay up. Prefer **chunked** deletes so one transaction does not hold the whole table and binlog.

Count first:

```sql
SELECT COUNT(*) AS orphan_items
FROM item
LEFT JOIN folder ON item.folder_id = folder.folder_id
WHERE folder.folder_id IS NULL;
```

If the count is zero, skip the DELETE.

Chunked loop (repeat until 0 rows affected). Adjust `5000` to load:

```sql
DELETE item
FROM item
LEFT JOIN folder ON item.folder_id = folder.folder_id
WHERE folder.folder_id IS NULL
LIMIT 5000;
```

One-shot production SQL (only if the orphan set is small):

```sql
DELETE item FROM item LEFT JOIN folder ON item.folder_id = folder.folder_id WHERE folder.folder_id IS NULL;
```

### Step 2 — Drop `snoozed_item_ibfk_1`

Native SQL on both OS. **Must complete before** the `item` rebuild so no child foreign key points at `item` during pt-osc swap.

Skip if the constraint is absent.

```sql
-- MySQL 8.0 (and 5.7 if InnoDB accepts INPLACE):
ALTER TABLE snoozed_item DROP FOREIGN KEY snoozed_item_ibfk_1, ALGORITHM=INPLACE, LOCK=NONE;
```

If 5.7 rejects `ALGORITHM=INPLACE`, omit the algorithm clause. `snoozed_item` is small; a copy is acceptable.

This runbook uses **native** `DROP FOREIGN KEY snoozed_item_ibfk_1` (no leading underscore). That is the real constraint name on the live table.

**pt-osc FK names:** Percona’s docs confirm that `DROP FOREIGN KEY` **via pt-osc** must use the name with a **leading underscore** (`_snoozed_item_ibfk_1`). pt-osc applies `--alter` to the ghost table, where constraint names are underscore-prefixed so they stay unique in the schema. If you ignore this and pass `snoozed_item_ibfk_1` to `--alter "DROP FOREIGN KEY …"`, pt-osc will not find the constraint on the ghost table. Do not use pt-osc for this DROP unless you intend that underscore form; leftover `_snoozed_item_new` after a failed run may also show `_snoozed_item_ibfk_1`.

### Step 3 — `folder` BIGINT

**Linux (pt-osc):** dry-run, then `--execute`. IceWarp may stay up.

```bash
pt-online-schema-change \
  --alter "MODIFY COLUMN folder_id BIGINT NOT NULL AUTO_INCREMENT, MODIFY COLUMN parent_folder_id BIGINT, MODIFY COLUMN sync_in_progress_folder_id BIGINT" \
  D=<wc_database>,t=folder \
  --no-check-alter \
  --alter-foreign-keys-method=rebuild_constraints \
  --max-load Threads_running=50 \
  --critical-load Threads_running=100 \
  --chunk-time=1 \
  --pause-file=/tmp/pt-osc.pause \
  --dry-run

pt-online-schema-change \
  --alter "MODIFY COLUMN folder_id BIGINT NOT NULL AUTO_INCREMENT, MODIFY COLUMN parent_folder_id BIGINT, MODIFY COLUMN sync_in_progress_folder_id BIGINT" \
  D=<wc_database>,t=folder \
  --no-check-alter \
  --alter-foreign-keys-method=rebuild_constraints \
  --max-load Threads_running=50 \
  --critical-load Threads_running=100 \
  --chunk-time=1 \
  --pause-file=/tmp/pt-osc.pause \
  --execute
```

Add DSN options as needed (`h=`, `u=`, `p=`, or `--defaults-file`). On 5.7 Toolkit builds use `--check-slave-lag`; on 8.0-aware builds use `--check-replica-lag` when you have replicas.

`--no-check-alter` is required because the primary key type changes. pt-osc warns; that is expected.

**Windows / Linux native fallback:**

```sql
ALTER TABLE folder MODIFY COLUMN folder_id BIGINT NOT NULL AUTO_INCREMENT, MODIFY COLUMN parent_folder_id BIGINT, MODIFY COLUMN sync_in_progress_folder_id BIGINT;
```

`folder` is usually small; a short lock is typical.

### Step 4 — `item` BIGINT + `FK_item_folder` (heavy)

This is the production-critical rebuild.

**Linux (pt-osc, IceWarp stays up).** Abort if `item` already has triggers you do not own. The swap takes a brief metadata lock; IceWarp may stall for seconds, not hours.

```bash
pt-online-schema-change \
  --alter "MODIFY COLUMN folder_id BIGINT NOT NULL, ADD CONSTRAINT FK_item_folder FOREIGN KEY (folder_id) REFERENCES folder(folder_id) ON DELETE CASCADE ON UPDATE CASCADE, MODIFY COLUMN item_id BIGINT NOT NULL AUTO_INCREMENT, MODIFY COLUMN size BIGINT NOT NULL, MODIFY COLUMN source_folder_id BIGINT, MODIFY COLUMN dummy_id BIGINT" \
  D=<wc_database>,t=item \
  --no-check-alter \
  --alter-foreign-keys-method=rebuild_constraints \
  --max-load Threads_running=50 \
  --critical-load Threads_running=100 \
  --chunk-time=1 \
  --pause-file=/tmp/pt-osc.pause \
  --dry-run

pt-online-schema-change \
  --alter "MODIFY COLUMN folder_id BIGINT NOT NULL, ADD CONSTRAINT FK_item_folder FOREIGN KEY (folder_id) REFERENCES folder(folder_id) ON DELETE CASCADE ON UPDATE CASCADE, MODIFY COLUMN item_id BIGINT NOT NULL AUTO_INCREMENT, MODIFY COLUMN size BIGINT NOT NULL, MODIFY COLUMN source_folder_id BIGINT, MODIFY COLUMN dummy_id BIGINT" \
  D=<wc_database>,t=item \
  --no-check-alter \
  --alter-foreign-keys-method=rebuild_constraints \
  --max-load Threads_running=50 \
  --critical-load Threads_running=100 \
  --chunk-time=1 \
  --pause-file=/tmp/pt-osc.pause \
  --execute
```

Create `/tmp/pt-osc.pause` to pause copying under load; remove the file to resume. Tune `--max-load` / `--chunk-time` for the host. Watch replica lag and binlog disk. **Do not `skip-log-bin`.**

If the job fails, drop leftover `_item_new` (name may vary) and pt-osc triggers on `item` before retrying. See [section 12](#12-failure-and-resume).

**Windows / Linux native fallback:** stop IceWarp first. Optional speed knobs: [section 11](#11-downtime-path-speed-knobs-native-alter-only) (standalone only, or replica-rebuild procedure).

```sql
ALTER TABLE item MODIFY COLUMN folder_id BIGINT NOT NULL, ADD CONSTRAINT FK_item_folder FOREIGN KEY (folder_id) REFERENCES folder(folder_id) ON DELETE CASCADE ON UPDATE CASCADE, MODIFY COLUMN item_id BIGINT NOT NULL AUTO_INCREMENT, MODIFY COLUMN size BIGINT NOT NULL, MODIFY COLUMN source_folder_id BIGINT, MODIFY COLUMN dummy_id BIGINT;
```

Monitor `SHOW FULL PROCESSLIST` and datadir free space. This blocks writes (and often reads) for the full COPY.

### Step 5 — `snoozed_item` BIGINT + `FK_snoozed_item_item`

**Linux (pt-osc)** — or native ALTER if the table is tiny:

```bash
pt-online-schema-change \
  --alter "MODIFY COLUMN snoozed_item_id BIGINT NOT NULL, ADD CONSTRAINT FK_snoozed_item_item FOREIGN KEY (snoozed_item_id) REFERENCES item(item_id) ON DELETE CASCADE ON UPDATE CASCADE" \
  D=<wc_database>,t=snoozed_item \
  --no-check-alter \
  --alter-foreign-keys-method=rebuild_constraints \
  --dry-run

pt-online-schema-change \
  --alter "MODIFY COLUMN snoozed_item_id BIGINT NOT NULL, ADD CONSTRAINT FK_snoozed_item_item FOREIGN KEY (snoozed_item_id) REFERENCES item(item_id) ON DELETE CASCADE ON UPDATE CASCADE" \
  D=<wc_database>,t=snoozed_item \
  --no-check-alter \
  --alter-foreign-keys-method=rebuild_constraints \
  --execute
```

**Windows / native:**

```sql
ALTER TABLE snoozed_item MODIFY COLUMN snoozed_item_id BIGINT NOT NULL, ADD CONSTRAINT FK_snoozed_item_item FOREIGN KEY (snoozed_item_id) REFERENCES item(item_id) ON DELETE CASCADE ON UPDATE CASCADE;
```

---

## 10. Restore durability and start IceWarp

If you used downtime speed knobs: remove the drop-in / revert `my.ini`, set `innodb_fast_shutdown=2`, restart mysqld, confirm `log_bin` and `innodb_doublewrite` are back to production values.

If you used `skip-log-bin` with replicas: **rebuild every replica** before putting IceWarp in production.

Windows: start the IceWarp services if you stopped them.

Smoke-test the webclient: list folders, open mail, snooze an item if that feature is used.

---

## 11. Downtime-path speed knobs (native ALTER only)

Use only when IceWarp is **stopped** and you use **native** `ALTER TABLE` (Windows primary path, or Linux fallback). **Do not** use these with pt-osc.

`skip-log-bin` is **forbidden** on a primary whose async replicas you intend to keep. See [section 8.3](#83-fast-standalone-style-alter-then-rebuild-replicas).

A crash in this window can lose committed work. Take a backup first.

**Linux drop-in** (example `/etc/my.cnf.d/zz-iw-bigint-window.cnf`). Confirm `!includedir` exists in `/etc/my.cnf`:

```ini
[mysqld]
skip-log-bin
innodb_doublewrite=OFF
innodb_flush_log_at_trx_commit=0
sync_binlog=0
```

**Windows:** the same `[mysqld]` lines in `my.ini`. Keep a copy of the original file.

Then:

```sql
SET GLOBAL innodb_fast_shutdown = 2;
```

Restart mysqld (`systemctl restart mysql` / `net stop` + `net start`). Wait until `mysql -e "SELECT 1"` succeeds.

`innodb_doublewrite` and `skip-log-bin` need a restart. `innodb_flush_log_at_trx_commit=0` is dynamic but still restore it afterward.

After the native ALTERs: **delete the drop-in / restore `my.ini`**, `SET GLOBAL innodb_fast_shutdown=2`, restart mysqld again, verify production `log_bin` and `innodb_doublewrite`, then start IceWarp.

---

## 12. Failure and resume

| Situation | Action |
|-----------|--------|
| Native COPY killed mid-ALTER | InnoDB restores the original table. Re-check column types. Retry. |
| pt-osc failed mid-copy | Drop leftover `_item_new` (or `_folder_new`, `_snoozed_item_new`) and `pt_osc` / `ptosc` triggers on the original table, then retry. `SHOW TRIGGERS FROM <wc_database>;` `SHOW TABLES LIKE '%_new';` |
| `skip-log-bin` was on | Do not trust replicas. Restore from the pre-window backup or rebuild replicas from the primary after a successful finish. |
| Disk full during COPY | Free space or restore from backup. Do not continue on a full datadir. |

---

## 13. MySQL 5.7 vs 8.0

| Topic | 5.7 | 8.0 |
|-------|-----|-----|
| INT→BIGINT PK | COPY / pt-osc copy | Same (not INSTANT) |
| Replica status | `SHOW SLAVE STATUS` | `SHOW REPLICA STATUS` (alias may exist) |
| pt-osc lag option | `--check-slave-lag` | `--check-replica-lag` on newer Toolkit |
| Redo log | `innodb_log_file_size` × files | 8.0.30+: `innodb_redo_log_capacity` |
| `max_execution_time` | n/a (or unused) | Set `0` on native sessions |
| `old_alter_table` | Set session `0` | Set session `0` (some hosts have it ON) |
| DROP FK INPLACE | Often works; omit ALGORITHM if rejected | `ALGORITHM=INPLACE, LOCK=NONE` |

---

## 14. Commands cheat-sheet

Set the database once:

```sql
USE `<wc_database>`;
```

### Instance and disk

```sql
SELECT VERSION();
SHOW VARIABLES WHERE Variable_name IN (
  'datadir','log_bin','innodb_doublewrite','innodb_flush_log_at_trx_commit',
  'sync_binlog','old_alter_table','innodb_buffer_pool_size',
  'innodb_log_file_size','innodb_log_files_in_group','innodb_redo_log_capacity'
);
```

```sql
SELECT table_name, table_rows,
       ROUND((data_length+index_length)/1024/1024/1024, 2) AS gb
FROM information_schema.tables
WHERE table_schema = DATABASE()
  AND table_name IN ('item','folder','snoozed_item');
```

Linux: `df -h` on the datadir filesystem.

Windows PowerShell (adjust drive): `(Get-PSDrive C).Free / 1GB`

### Redo size (GiB)

```sql
-- 5.7 / 8.0.29-
SELECT ROUND(@@innodb_log_file_size * @@innodb_log_files_in_group / 1024/1024/1024, 2) AS redo_gib;

-- 8.0.30+
SHOW VARIABLES LIKE 'innodb_redo_log_capacity';
```

### Types and foreign keys

```sql
SELECT table_name, column_name, column_type
FROM information_schema.columns
WHERE table_schema = DATABASE()
  AND (
    (table_name='folder' AND column_name IN ('folder_id','parent_folder_id','sync_in_progress_folder_id'))
    OR (table_name='item' AND column_name IN ('item_id','folder_id','size','source_folder_id','dummy_id'))
    OR (table_name='snoozed_item' AND column_name IN ('snoozed_item_id'))
  )
ORDER BY table_name, column_name;
```

```sql
SELECT constraint_name, table_name, referenced_table_name
FROM information_schema.referential_constraints
WHERE constraint_schema = DATABASE()
  AND table_name IN ('item','snoozed_item');
```

```sql
SHOW TRIGGERS FROM `<wc_database>` WHERE `Table` = 'item';
```

### Replicas

MySQL 8.0: `SHOW REPLICA STATUS\G`

MySQL 5.7: `SHOW SLAVE STATUS\G`

### Orphans

```sql
SELECT COUNT(*) AS orphan_items
FROM item LEFT JOIN folder ON item.folder_id = folder.folder_id
WHERE folder.folder_id IS NULL;
```

### pt-osc dry-run (Linux)

See [step 3–5](#step-3--folder-bigint) for full flags. Minimal:

```bash
pt-online-schema-change --version
```

### “Done” verification

```sql
SELECT column_name, column_type FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name='item'
  AND column_name IN ('item_id','folder_id','size');

SELECT constraint_name, table_name
FROM information_schema.referential_constraints
WHERE constraint_schema = DATABASE()
  AND constraint_name IN ('FK_item_folder','FK_snoozed_item_item','snoozed_item_ibfk_1');
```

Expect `bigint` columns, `FK_item_folder` and `FK_snoozed_item_item` present, `snoozed_item_ibfk_1` absent.

# IceWarp webclient DB migration benchmark

Generated: `2026-08-15T17:02:48Z`

This report times the INT→BIGINT and folder-redesign SQL pipeline against isolated `test_wc_*` databases.

## Environment

| Item | Value |
|------|-------|
| Host | TDBSV1 (172.16.5.100) |
| OS | AlmaLinux 9.8 / x86_64 |
| CPU cores | 8 |
| RAM | 15731 MiB |
| MySQL version | 8.0.46-37 |
| MySQL client | mysql  Ver 8.0.46-37 for Linux on x86_64 (Percona Server (GPL), Release 37, Revision 39e2b60e) |
| `innodb_buffer_pool_size` | 12.00 GiB (12884901888 bytes) |
| `innodb_ddl_threads` | 4 |
| `innodb_parallel_read_threads` | 4 |
| `innodb_autoinc_lock_mode` | 2 |
| datadir | `/var/lib/mysql/` |
| datadir free (at collection) | 972.15 GiB / 999.51 GiB |
| `secure_file_priv` | `/var/lib/mysql-files/` |
| TSV staging | `/var/lib/mysql-files` |
| Work directory | `/var/tmp/wc_bench` |
| Python | Python 3.9.25 |

Additional MySQL variables:

| Variable | Value |
|----------|-------|
| `datadir` | /var/lib/mysql/ |
| `innodb_autoinc_lock_mode` | 2 |
| `innodb_buffer_pool_size` | 12884901888 |
| `innodb_ddl_threads` | 4 |
| `innodb_file_per_table` | ON |
| `innodb_flush_log_at_trx_commit` | 0 |
| `innodb_page_size` | 16384 |
| `innodb_parallel_read_threads` | 4 |
| `max_allowed_packet` | 67108864 |
| `old_alter_table` | ON |
| `secure_file_priv` | /var/lib/mysql-files/ |
| `sync_binlog` | 1 |
| `version` | 8.0.46-37 |
| `version_comment` | Percona Server (GPL), Release 37, Revision 39e2b60e |
| `version_compile_machine` | x86_64 |
| `version_compile_os` | Linux |

## Seeding method

The empty schema is created with production indexes (and optional source FKs). Rows are loaded with `LOAD DATA INFILE` under `FOREIGN_KEY_CHECKS=0` / `UNIQUE_CHECKS=0`. Post-load `ALTER TABLE ... ADD INDEX` / `ADD CONSTRAINT` is not used (it can crash mysqld on a populated `item` table).

## Per-tier results

### 10M — `test_wc_10m` (10000000 rows)


#### Seed / preparation

| Metric | Value |
|--------|-------|
| Accounts | 1000 |
| Folders | 10000 |
| Items | 10000000 |
| Snoozed items | 10000 |
| Seed mode | load_into_indexed_schema |
| Total seed time | 23m 21.7s |
| LOAD DATA time | 23m 21.7s |
| Index rebuild time | 0.0ms |
| Source FK time | 0.0 s |
| Workers / batch | 4 / 250000 |

#### Table size (data + index)

| Phase | Data | Index | Total |
|-------|------|-------|-------|
| Pre-migration | 3.90 GiB | 2.75 GiB | 6.65 GiB |
| Post-migration | 3.92 GiB | 3.59 GiB | 7.51 GiB |
| Growth | | | 879.44 MiB |

Pre-migration per table:

| Table | Rows (est.) | Data | Index | Total |
|-------|-------------|------|-------|-------|
| `folder` | 9795 | 1.52 MiB | 3.64 MiB | 5.16 MiB |
| `item` | 9744914 | 3.89 GiB | 2.75 GiB | 6.64 GiB |
| `metadata` | 0 | 16.0 KiB | 0 B | 16.0 KiB |
| `snoozed_item` | 9653 | 1.52 MiB | 528.0 KiB | 2.03 MiB |

Post-migration per table:

| Table | Rows (est.) | Data | Index | Total |
|-------|-------------|------|-------|-------|
| `account` | 1000 | 96.0 KiB | 80.0 KiB | 176.0 KiB |
| `account_folders` | 7962 | 1.52 MiB | 560.0 KiB | 2.06 MiB |
| `folders` | 9839 | 1.52 MiB | 240.0 KiB | 1.75 MiB |
| `item` | 9744454 | 3.92 GiB | 3.59 GiB | 7.51 GiB |
| `metadata` | 1 | 16.0 KiB | 0 B | 16.0 KiB |
| `snoozed_item` | 9834 | 448.0 KiB | 288.0 KiB | 736.0 KiB |

Total migration runtime: **5796.560 s**

#### Statements

| Step | Name | Status | Duration | Human | Data Δ | Index Δ | InnoDB written Δ |
|------|------|--------|----------|-------|--------|---------|------------------|
| 1 | `drop_fk_item_folder` | ok | 0.048 s | 48.0ms | 0 B | 0 B | 167.5 KiB |
| 1 | `drop_fk_snoozed_item` | ok | 0.030 s | 30.2ms | 0 B | 0 B | 283.0 KiB |
| 2 | `folder_id_to_bigint` | ok | 1.138 s | 1.14s | 0 B | 128.0 KiB | 11.53 MiB |
| 3 * | `item_folder_id_to_bigint` | ok | 1931.076 s | 32m 11.1s | -79691776 B | 410.84 MiB | 228.85 GiB |
| 4 | `delete_orphan_items` | ok | 54.080 s | 54.08s | 0 B | 0 B | 528.72 MiB |
| 4 | `delete_orphan_snoozed_items` | ok | 0.139 s | 139.2ms | 0 B | 0 B | 512 B |
| 5 | `add_fk_item_folder` | ok | 1803.533 s | 30m 3.5s | 0 B | 0 B | 231.23 GiB |
| 6 * | `item_pk_and_int_to_bigint` | ok | 1998.254 s | 33m 18.3s | 101.00 MiB | 446.08 MiB | 246.57 GiB |
| 7 | `add_fk_snoozed_item_item` | failed | 0.027 s | 26.8ms | 0 B | 0 B | 0 B |
| 7 | `fix_snoozed_item_id_bigint` | ok | 0.767 s | 766.9ms | 0 B | 1.00 MiB | 29.63 MiB |
| 7 | `add_fk_snoozed_item_item_retry` | ok | 1.178 s | 1.18s | 0 B | 0 B | 4.09 MiB |
| 8 | `rename_folder_to_folders` | ok | 0.057 s | 56.6ms | 1.52 MiB | 3.77 MiB | 126.0 KiB |
| 8 | `create_table_account` | ok | 0.044 s | 43.8ms | 16.0 KiB | 16.0 KiB | 54.5 KiB |
| 8 | `insert_account_distinct` | ok | 0.130 s | 129.8ms | 0 B | 0 B | 479.0 KiB |
| 8 | `analyze_account` | ok | 0.007 s | 6.8ms | 0 B | 0 B | 11.0 KiB |
| 8 | `folders_add_own_acc_id` | ok | 0.230 s | 230.0ms | 0 B | 0 B | 73.5 KiB |
| 8 | `folders_update_own_acc_id` | ok | 1.195 s | 1.19s | 0 B | 0 B | 6.62 MiB |
| 8 | `create_table_account_folders` | ok | 0.073 s | 72.8ms | 16.0 KiB | 32.0 KiB | 100.0 KiB |
| 8 | `insert_account_folders` | ok | 1.070 s | 1.07s | 0 B | 0 B | 6.27 MiB |
| 8 | `analyze_account_folders` | ok | 0.015 s | 14.9ms | 0 B | 0 B | 9.5 KiB |
| 8 | `folders_drop_index_FdrName` | ok | 0.052 s | 51.7ms | 0 B | 0 B | 73.0 KiB |
| 8 | `folders_drop_index_IDX_folder_name` | ok | 0.337 s | 336.9ms | 0 B | 0 B | 111.5 KiB |
| 8 | `folders_drop_index_IDX_folder_parent` | ok | 0.048 s | 47.5ms | 0 B | 0 B | 57.5 KiB |
| 8 | `folders_drop_index_IDX_folder_account` | ok | 0.042 s | 42.1ms | 0 B | 0 B | 62.0 KiB |
| 8 | `folders_drop_account_columns_add_fk` | ok | 0.697 s | 696.8ms | 0 B | -3702784 B | 2.49 MiB |
| 9 | `snoozed_add_snoozed_acc_id` | ok | 0.193 s | 192.5ms | 0 B | 0 B | 40.0 KiB |
| 9 | `snoozed_update_acc_id` | ok | 0.987 s | 987.2ms | 0 B | 0 B | 3.82 MiB |
| 9 | `snoozed_drop_index_IDX_snoozed_date` | ok | 0.182 s | 182.1ms | 0 B | 0 B | 25.5 KiB |
| 9 | `snoozed_drop_account_id_add_fk` | ok | 0.577 s | 576.8ms | -1130496 B | -1310720 B | 1.56 MiB |
| 9 | `snoozed_create_index_IDX_snoozed_date` | ok | 0.330 s | 329.7ms | 0 B | 0 B | 363.5 KiB |
| 10 | `create_view_folder` | ok | 0.018 s | 18.2ms | 0 B | 0 B | 18.0 KiB |
| 10 | `insert_metadata_version` | ok | 0.008 s | 7.7ms | 0 B | 0 B | 1.0 KiB |

#### Bottlenecks (>80% of tier migration time)

No single statement exceeded 80% of this tier's migration time.


## Comparison matrix

Duration in seconds for each statement. **Step 3** (`item.folder_id` → BIGINT) and **Step 6** (PK / INT→BIGINT rebuild of `item`) are the expected heavy DDL.

| Step | Statement | 10M | |------|-----------|------------|| 1 | `drop_fk_item_folder` | 0.048 | | 1 | `drop_fk_snoozed_item` | 0.030 | | 2 | `folder_id_to_bigint` | 1.138 | | 3 **\*** | `item_folder_id_to_bigint` | 1931.076 | | 4 | `delete_orphan_items` | 54.080 | | 4 | `delete_orphan_snoozed_items` | 0.139 | | 5 | `add_fk_item_folder` | 1803.533 | | 6 **\*** | `item_pk_and_int_to_bigint` | 1998.254 | | 7 | `add_fk_snoozed_item_item` | FAILED | | 8 | `rename_folder_to_folders` | 0.057 | | 8 | `create_table_account` | 0.044 | | 8 | `insert_account_distinct` | 0.130 | | 8 | `analyze_account` | 0.007 | | 8 | `folders_add_own_acc_id` | 0.230 | | 8 | `folders_update_own_acc_id` | 1.195 | | 8 | `create_table_account_folders` | 0.073 | | 8 | `insert_account_folders` | 1.070 | | 8 | `analyze_account_folders` | 0.015 | | 8 | `folders_drop_index_FdrName` | 0.052 | | 8 | `folders_drop_index_IDX_folder_name` | 0.337 | | 8 | `folders_drop_index_IDX_folder_parent` | 0.048 | | 8 | `folders_drop_index_IDX_folder_account` | 0.042 | | 8 | `folders_drop_account_columns_add_fk` | 0.697 | | 9 | `snoozed_add_snoozed_acc_id` | 0.193 | | 9 | `snoozed_update_acc_id` | 0.987 | | 9 | `snoozed_drop_index_IDX_snoozed_date` | 0.182 | | 9 | `snoozed_drop_account_id_add_fk` | 0.577 | | 9 | `snoozed_create_index_IDX_snoozed_date` | 0.330 | | 10 | `create_view_folder` | 0.018 | | 10 | `insert_metadata_version` | 0.008 | | extra | `fix_snoozed_item_id_bigint` | 0.767 | | extra | `add_fk_snoozed_item_item_retry` | 1.178 | 
\* Step 3 and Step 6 are the production-critical `item` rebuilds.

## Recommendations for production

These are planning notes from this benchmark, not a go-live runbook.

1. **Do not run this playbook against any production database.** Use a restore of production on comparable hardware, or treat these numbers as a lower bound only if the test host is smaller than production.
2. **COPY DDL needs spare disk equal to the table.** Step 3 and Step 6 rewrite `item`. Keep at least `data_length + index_length` free on the datadir (this playbook budgets 2.5×). Monitor `df` during the run.
3. **Buffer pool vs RAM, not only vs table size.** COPY DDL plus an oversized `innodb_buffer_pool_size` will OOM-kill mysqld. This host had the kernel kill mysqld during `ALTER TABLE item DROP FOREIGN KEY` while RSS was ~15.7 GiB on ~16 GiB RAM and swap was exhausted. Leave several GiB of RAM outside the buffer pool for sort/tmp/COPY.
4. **`old_alter_table=ON` turns DROP FOREIGN KEY into a full table copy** (`state: copy to tmp table`). MySQL 8 can drop an InnoDB FK in-place. This harness `SET SESSION old_alter_table=0` on every timed statement and uses `ALGORITHM=INPLACE, LOCK=NONE` on Step 1. Production must do the same or DROP FK will rebuild `item` and can OOM.
5. **Locking / availability.** In-place `ALTER TABLE ... MODIFY` to BIGINT on a PK or indexed column typically rebuilds the table and blocks writes (and often reads). If the measured Step 3 or Step 6 duration is longer than the allowed maintenance window, evaluate `pt-online-schema-change` or `gh-ost` (or MySQL 8 instant/inplace only where the manual confirms the change qualifies — INT→BIGINT PK does **not**).
6. **Temporary InnoDB knobs (maintenance window only, then restore):**
   - `innodb_flush_log_at_trx_commit=2` (or `0` on a dedicated box) and `sync_binlog=0` cut fsync cost during the copy; they raise crash-loss risk.
   - `innodb_autoinc_lock_mode=2` (interleaved) helps parallel loaders; it is not runtime-changeable without a restart on many versions.
   - MySQL 8.0.27+: raise `innodb_ddl_threads` / `innodb_parallel_read_threads` for parallel sort/rebuild if CPU and I/O have headroom.
7. **Foreign keys.** Step 1 is a no-op unless FKs exist. This harness adds them on the empty schema (`apply_source_fks_after_seed`) and loads with `FOREIGN_KEY_CHECKS=0`. Step 5/7 FK validation scans `item` / `snoozed_item` — budget that time. Step 6 leaves `snoozed_item.snoozed_item_id` as INT while `item.item_id` becomes BIGINT; production SQL should convert `snoozed_item_id` before Step 7 (`fix_fk_type_mismatch` in this project).
8. **Orphan deletes (Step 4).** With consistent seed data the `DELETE ... LEFT JOIN` still scans `item`. On 100M/500M/1B that can rival an ALTER. Run `EXPLAIN` / consider chunked deletes in production if orphans are rare but the nested-loop join is not.
9. **Step 8–9** are metadata-scale (folders/accounts), not `item`-scale, unless you have millions of folders. Still avoid mixing them in the same lock window as Step 6.
10. **Start with 1M, then 5M, then 10M, then 100M, on production-like storage (same SSD/RAID, same `innodb_io_capacity`).** Do not extrapolate 1M durations linearly to 500M or 1B; rebuilds are roughly linear in table bytes plus a large constant for index build, and can go superlinear when the working set spills the buffer pool.


# IceWarp webclient DB migration benchmark

Ansible project that SSHs onto an existing **MySQL 8** server, creates isolated test databases, bulk-loads realistic `item` rows, times every statement in the IceWarp webclient INT→BIGINT / folder-redesign migration, and writes `reports/migration_benchmark_report.md`.

It never touches any already existing database. Each scale uses a disposable database:

| Rows | Database |
|------|----------|
| 1,000,000 | `test_wc_1m` |
| 10,000,000 | `test_wc_10m` |
| 100,000,000 | `test_wc_100m` |
| 500,000,000 | `test_wc_500m` |
| 1,000,000,000 | `test_wc_1b` |
| other | `test_wc_<N>r` |

## Requirements

On the **control node**: Ansible 2.12+ (stdlib modules only; no collections).

On the **MySQL host** (the inventory machine):

- SSH access
- MySQL 8.0+ client and server (`utf8mb4_0900_as_ci` is required by Step 8)
- Python 3.6+
- `innodb_file_per_table=ON`
- Privilege to `CREATE DATABASE` / `DROP DATABASE`, `ALTER`, `INDEX`, `REFERENCES`
- `FILE` privilege and a usable `secure_file_priv` directory (for `LOAD DATA INFILE`)

This playbook does **not** install MySQL.

## Disk and time (order of magnitude)

InnoDB `COPY` DDL duplicates the table. Budget **~2.5×** the loaded size as free space on the datadir.

| Scale | Data + indexes (rough) | Free space to plan | Runtime |
|-------|------------------------|--------------------|---------|
| 1M | ~2 GiB | ~5 GiB | minutes |
| 10M | ~20 GiB | ~50 GiB | tens of minutes |
| 100M | ~200 GiB | ~500 GiB | hours |
| 500M | ~1 TiB | ~2.5 TiB | many hours |
| 1B | ~2 TiB | ~4–5 TiB | many hours to days |

The 1B tier refuses to start unless you pass `-e confirm_1b=true`.

## Configure

1. Edit `inventory.ini`: `ansible_host`, `ansible_user`, `mysql_user`, `mysql_password` (and `mysql_socket` if you do not use TCP).
2. Review knobs in `group_vars/all.yml` (`batch_size`, `seed_workers`, `keep_database`, …).

`DISABLE KEYS` is a no-op on InnoDB. Seeding drops secondary indexes, `LOAD DATA INFILE`s in batches, then rebuilds the production indexes **before** migration so the timed ALTERs see a realistic table.

## Run

```bash
# 1M only (recommended first run)
ansible-playbook site.yml -e '{"row_counts": [1000000]}'

# Same via preset
ansible-playbook site.yml -e row_count_preset=1m

# 10M
ansible-playbook site.yml -e row_count_preset=10m

# 100M
ansible-playbook site.yml -e row_count_preset=100m

# 500M
ansible-playbook site.yml -e row_count_preset=500m

# 1B (explicit confirmation required)
ansible-playbook site.yml -e row_count_preset=1b -e confirm_1b=true

# All named scales (1B still needs confirm_1b)
ansible-playbook site.yml -e row_count_preset=all -e confirm_1b=true
```

Optional:

```bash
# Keep test_wc_* after the run for manual inspection
ansible-playbook site.yml -e row_count_preset=1m -e keep_database=true

# Skip datadir free-space assertion
ansible-playbook site.yml -e row_count_preset=1m -e skip_disk_check=true

# Faster loads on a dedicated box (GLOBAL tunables; restored in an always block)
ansible-playbook site.yml -e row_count_preset=1m -e enable_load_tuning=true
```

Output:

- `reports/migration_benchmark_report.md` — environment, per-step timings, comparison matrix, bottlenecks, recommendations
- `reports/benchmark_run.json` — full machine-readable results
- `reports/results_<database>_statements.json` — crash-safe per-statement log

## Migration SQL gap

Step 6 converts `item.item_id` to `BIGINT` while `snoozed_item.snoozed_item_id` stays `INT`. MySQL then rejects the Step 7 foreign key (incompatible types). With `fix_fk_type_mismatch: true` (default) the playbook times an extra `MODIFY snoozed_item_id BIGINT` and retries the FK so Steps 8–10 can run.

## Safety

- Target names are always `test_wc_*`.
- `enable_load_tuning` changes **global** `innodb_flush_log_at_trx_commit` and `sync_binlog` on the instance. Leave it off on shared servers.
- Default `keep_database: false` drops the test DB after metrics so later tiers (100M / 500M / 1B) can share disk.

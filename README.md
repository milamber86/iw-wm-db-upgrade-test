# IceWarp webclient DB migration benchmark

Ansible project that SSHs onto an existing **MySQL 8** server, creates isolated test databases, bulk-loads realistic `item` rows, times every statement in the IceWarp webclient INT→BIGINT / folder-redesign migration, and writes timestamped files under `reports/`.

It never touches any already existing database. Each scale uses a disposable database:

| Rows | Database |
|------|----------|
| 1,000,000 | `test_wc_1m` |
| 3,000,000 | `test_wc_3m` |
| 5,000,000 | `test_wc_5m` |
| 10,000,000 | `test_wc_10m` |
| 15,000,000 | `test_wc_15m` |
| 20,000,000 | `test_wc_20m` |
| 30,000,000 | `test_wc_30m` |
| 50,000,000 | `test_wc_50m` |
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
| 3M | ~6 GiB | ~15 GiB | minutes |
| 5M | ~10 GiB | ~25 GiB | tens of minutes |
| 10M | ~20 GiB | ~50 GiB | tens of minutes |
| 15M | ~30 GiB | ~75 GiB | tens of minutes |
| 20M | ~40 GiB | ~100 GiB | ~1 hour |
| 30M | ~60 GiB | ~150 GiB | ~1–2 hours |
| 50M | ~100 GiB | ~250 GiB | ~2–4 hours |
| 100M | ~200 GiB | ~500 GiB | hours |
| 500M | ~1 TiB | ~2.5 TiB | many hours |
| 1B | ~2 TiB | ~4–5 TiB | many hours to days |

The 1B tier refuses to start unless you pass `-e confirm_1b=true`.

## Configure

1. Edit `inventory.ini`: `ansible_host`, `ansible_user`, `mysql_user`, `mysql_password` (and `mysql_socket` if you do not use TCP).
2. Review knobs in `group_vars/all.yml` (`batch_size`, `seed_workers`, `keep_database`, …).

`DISABLE KEYS` is a no-op on InnoDB. The empty schema is created with production indexes (and the optional snoozed_item FK), then `LOAD DATA INFILE` runs with `FOREIGN_KEY_CHECKS=0` and `UNIQUE_CHECKS=0`. Adding FKs or secondary indexes after a full `item` table can take hours and crash mysqld; do not rebuild them post-load.

By default (`enable_fast_seed: true`) the playbook writes `/etc/my.cnf.d/zz-wc-bench-fast-seed.cnf` (`skip-log-bin`, `innodb_doublewrite=OFF`, `innodb_flush_log_at_trx_commit=0`, `sync_binlog=0`), restarts mysqld, loads data, removes the drop-in, and restarts again **before** ANALYZE and timed ALTERs. That affects the **whole instance**, not only `test_wc_*`. Use `-e enable_fast_seed=false` on primaries with async replicas (skip-log-bin would not replicate the load). Override `mysql_service_name` (default `mysql`) and `mysql_conf_dropin_dir` (default `/etc/my.cnf.d`; Debian often `/etc/mysql/mysql.conf.d`).

## Run

```bash
# 1M only (recommended first run)
ansible-playbook site.yml -e '{"row_counts": [1000000]}'

# Same via preset
ansible-playbook site.yml -e row_count_preset=1m

# 3M
ansible-playbook site.yml -e row_count_preset=3m

# 5M
ansible-playbook site.yml -e row_count_preset=5m

# 10M
ansible-playbook site.yml -e row_count_preset=10m

# 15M
ansible-playbook site.yml -e row_count_preset=15m

# 20M
ansible-playbook site.yml -e row_count_preset=20m

# 30M
ansible-playbook site.yml -e row_count_preset=30m

# 50M
ansible-playbook site.yml -e row_count_preset=50m

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

# Faster loads without a mysqld restart (SET GLOBAL only; restored afterwards)
ansible-playbook site.yml -e row_count_preset=1m -e enable_fast_seed=false -e enable_load_tuning=true

# Replica / cluster hosts: do not use skip-log-bin during seed
ansible-playbook site.yml -e row_count_preset=1m -e enable_fast_seed=false
```

Output:

- `reports/migration_benchmark_report_YYYYMMDDThhmmss.md` — environment, per-step timings, comparison matrix, bottlenecks, recommendations
- `reports/benchmark_run_YYYYMMDDThhmmss.json` — full machine-readable results
- `reports/results_<database>_statements_YYYYMMDDThhmmss.json` — crash-safe per-statement log

## Migration SQL

Timed statements in `files/migration_steps.yml` match the production script: orphan `DELETE` first, drop `snoozed_item_ibfk_1`, widen `folder` IDs, then one combined `item` ALTER (BIGINT columns + `FK_item_folder`), then `snoozed_item` BIGINT + FK together. The harness adds only the snoozed FK on the empty schema so that DROP is real; it does not pre-create `FK_item_folder`.

## Safety

- Target names are always `test_wc_*`.
- `enable_fast_seed` (default true) restarts mysqld twice per tier with `skip-log-bin` and `innodb_doublewrite=OFF` during LOAD DATA only. Never leave this on for a primary with async replicas.
- `enable_load_tuning` changes **global** `innodb_flush_log_at_trx_commit` and `sync_binlog` without a restart. Leave it off on shared servers. It is skipped when `enable_fast_seed` is true (those knobs are in the drop-in).
- Default `keep_database: false` drops the test DB after metrics so later tiers (100M / 500M / 1B) can share disk.

# IceWarp 14.3 -> 14.4 upgrade test bed (Infralab)

The same repo also carries a repeatable, Ansible-orchestrated upgrade harness
that provisions a 16 vCPU / 128 GB AlmaLinux 9 VM on the Infralab hypervisor,
installs IceWarp 14.3 + Percona Server 8.0, restores the newest production
`.sql.gz` dumps, validates accounts/services/`iwmon2`, then runs the unattended
14.4 installer while capturing per-`ALTER` timings into
`reports/upgrade_benchmark_*.{md,json}`.

See `.kilo/plans/1791898144101-icewarp-143-144-upgrade-test.md` for the design.

## Layout

```
inventory/iwupgrade/hosts.yml               hypervisors, production, iwtarget (runtime)
inventory/iwupgrade/group_vars/all/main.yml versions, tuning, VM/prod settings
inventory/iwupgrade/.vault.yml.example      secrets template (-> group_vars/all/vault.yml)
playbooks/iw-provision.yml                  play 1 only (create VM + add_host)
playbooks/iw-upgrade-site.yml               full orchestrator
roles/iw_provision                          vps_vm wrapper + IP discovery + generated inventory
roles/iw_base                               OS prereqs, hosts, sysctl, firewall
roles/mysql_fastload                        Percona 8.0 + fast-import drop-in + apply/revert
roles/iw_install                            pinned installer, auto upgrade detection, license (fresh/upgrade)
roles/iw_restore                            prod firewall exception, scp pull, import, DB remap
roles/iw_validate                           account export, iwmon2 config regen, probes
roles/iw_upgrade                            slow-log capture, run 14.4 installer, settle
roles/iw_timing                             parse ALTER rows, render the benchmark report
roles/iw_dbprobe                            read-only DB/backup probe + cross-instance overview
scripts/parse_alter_timings.py              slow-log TSV -> timings JSON
scripts/probe_icewarp_dbs.py                read-only DB/backup probe (runs on the target)
scripts/summarize_db_probe.py               per-server probe JSON -> combined overview
playbooks/iw-db-probe.yml                   read-only probe of the `probes` group
files/iw_my_fastload.cnf                    Percona fast-import drop-in
templates/iwmon2_config.yml.j2              iwmon2 config, accounts from tool.sh export
```

## Prerequisites

1. `VPSCTL_TOKEN` exported on the control node (vpsctl bootstrap admin token).
2. A template (`tpl_L1_OB_almalinux9.qcow2`) and network (`br0-net`) registered in
   vpsctl; confirm with `vpsctl template list` / `vpsctl network list`.
3. `inventory/iwupgrade/group_vars/all/vault.yml` created from
   `inventory/iwupgrade/.vault.yml.example`, filled with the prod SSH target/key
   and MySQL passwords, and encrypted.
4. Cloud-init user/SSH key (or a documented existing login) for the guest.

`ansible.cfg` `roles_path` includes the infralab-hv-orc roles so the `vps_vm`
role resolves without copying it.

## Run

```bash
export VPSCTL_TOKEN=...

# Provision only (idempotent: the VM is looked up by name first)
ansible-playbook -i inventory/iwupgrade playbooks/iw-provision.yml

# Full cycle
ansible-playbook -i inventory/iwupgrade playbooks/iw-upgrade-site.yml --ask-vault-pass

# Include the production restore (explicit, destructive-adjacent opt-in)
ansible-playbook -i inventory/iwupgrade playbooks/iw-upgrade-site.yml \
  --ask-vault-pass -e confirm_prod_transfer=true
```

## Notes

- **Engine:** Percona Server 8.0 (not MariaDB); `/etc/my.cnf` is written before
  the first server start so the datadir is initialized with
  `lower_case_table_names=1` (immutable afterwards).
- **Fast import:** `files/iw_my_fastload.cnf` (`skip-log-bin`,
  `innodb_doublewrite=OFF`, `innodb_flush_log_at_trx_commit=0`, `sync_binlog=0`)
  is applied only around the restore and reverted before the upgrade so ALTER
  timings are measured on a normal instance.
- **Prod safety:** the SSH exception is tagged `iw-upgrade-test` and removed in an
  `always` block; the role asserts its removal. The transfer runs from the guest
  and reaches prod NATed as `185.138.220.201`, so the rule permits that source.
- **Prod access model:** prod is reached as the operator account (e.g.
  `beranek`), not root. Root is obtained the way production hosts expect —
  passwordless only for an allow-list such as
  `NOPASSWD: /bin/su, /usr/bin/sudo, /bin/bash` (`sudo su -`). The `prod` host
  therefore sets `ansible_shell_executable: /bin/bash`, so Ansible's `sudo`
  become runs `sudo -n /bin/bash -c ...` and matches that allow-list instead of
  asking for a password.
- **Accounts storage:** IceWarp persists accounts in an embedded
  `config/accounts.db` on a fresh install and refuses to switch to MySQL while
  that file exists (the ODBC/PDO settings silently revert to `sqlite:`).
  `iw_restore` removes `config/accounts.db` and `accounts.migrated` (with
  IceWarp stopped) before setting the connection string, after which IceWarp
  adopts the restored MySQL `acc` database.
- **Installer selection:** the 14.4-over-14.3 step relies on the installer's own
  `--auto` upgrade detection; the clean-install guard must NOT be patched (that
  guard is the upgrade-vs-new decision, and forcing it makes the installer
  re-install without applying the 14.4 payload). The installer script is located
  inside the target package's extracted directory, so older extracted versions
  in the download dir are never picked up.
- **Missing dumps:** component types (`acc/asp/grw/dc/eas/wc`) are parsed from
  the backup filename and absent types are skipped; the accounts (`acc`) dump is
  required. The webclient dump is disabled in the current production backup
  script, so `wc` is often absent and falls back to the empty local DB.
- **ALTER capture:** the 14.4 schema changes are issued by IceWarp, so the MySQL
  slow log is used (`log_output=TABLE`, `log_slow_admin_statements=ON`,
  `long_query_time=0`) with a settle loop; `scripts/timed_sql.py` stays available
  for measured, self-issued statements.
- **iwmon2:** its config is force-regenerated (the stock deploy only re-renders
  when the file is missing or the Prom stack is enabled) and `testmail` is
  derived from the first restored account.

# IceWarp database & backup probe (read-only)

`playbooks/iw-db-probe.yml` logs into every server in the `probes` inventory
group, connects with the IceWarp DB credentials already on the host
(`/root/.my.cnf`), and reports — **without modifying anything** — the size and
row count of every table in every IceWarp database, plus the newest backup set.

The `probes` group in `inventory/iwupgrade/hosts.yml` contains the production
hosts (via the `production` group) and the test cloud server `onice`
(`icewarp.onice.io`, reached directly as root). The production hosts are reached
as the operator account and elevated with the same `sudo` allow-list as the
restore (`ansible_shell_executable: /bin/bash` + `become: true`), so the probe
can read `/root/.my.cnf`; `onice` sets `iw_dbprobe_become: false`.

## What it collects

- **Databases:** every IceWarp database, detected as a schema named like
  `dbiwc*` (`iw_dbprobe_db_pattern`) or containing an IceWarp signature table
  (`account`, `Users`, `item`, `folder`, `folders`, `mailbox`, `metadata`,
  `calendars`, `thread`). Override with `iw_dbprobe_db_include` /
  `iw_dbprobe_db_exclude`.
- **Tables:** for every base table, the estimated row count and the accurate
  `DATA_LENGTH` / `INDEX_LENGTH` / `DATA_FREE` byte counters, plus per-database
  totals and the top table by rows and by data.
- **Backups:** the newest `bck_db_<type>_<db><stamp>.sql.gz` set under
  `iw_dbprobe_backup_dir` (default `/mnt/data/backup`), each file's size and how
  it compares to the matching database, the newest `bck_acc_*.csv` account
  export, the newest-set timestamp/age and how many sets are kept.
- **MySQL:** server version and the endpoint (`host`/`port`) from the option
  file. The client **user and password are never read or reported**.

## Read-only guarantees

The probe only issues `SELECT`s: `information_schema` queries and, with
`-e iw_dbprobe_row_count_mode=exact`, one `COUNT(*)` per table (still read-only,
but it scans each table — use `estimate`, the default, for large instances). It
never writes to databases, config, services or backups, and the JSON is built in
memory on the target and saved on the control node.

## Run

```bash
# Probe every host in the `probes` group
ansible-playbook -i inventory/iwupgrade playbooks/iw-db-probe.yml

# One host only
ansible-playbook -i inventory/iwupgrade playbooks/iw-db-probe.yml --limit onice

# Exact COUNT(*) instead of information_schema estimates
ansible-playbook -i inventory/iwupgrade playbooks/iw-db-probe.yml -e iw_dbprobe_row_count_mode=exact
```

### Real host list

`iw_dbprobe_targets` selects the hosts (default `probes`), so the same playbook
runs against the real inventory `inventory/iwcloudlist_all.ini`:

```bash
# canary_group_6 is intentionally excluded (non-public IPs / different SSH)
ansible-playbook -i inventory/iwcloudlist_all.ini playbooks/iw-db-probe.yml \
  -e iw_dbprobe_targets='canary_group_1:canary_group_2:canary_group_3:canary_group_4:canary_group_5' \
  -f 60
```

`inventory/group_vars/all.yml` (loaded only for that inventory) sets the real
access model: log in as the operator account `beranek` with the existing key and
elevate with `sudo su -` (sudoers allows NOPASSWD only for
`/bin/su, /usr/bin/sudo, /bin/bash`). The probe role elevates only the on-host
tasks with `become: "{{ iw_dbprobe_become }}"`; a global `ansible_become` would
make the delegated control-node tasks try to sudo too.

The instance short name comes from the inventory FQDN (`vps_hostname`, else the
inventory name, else `ansible_host`): hosts under `onice.io` use the first label
(`support3.onice.io` -> `support3`), everything else keeps the full name
(`mail.icewarp.com`, `mx.ftcz.net`). The connection IP is recorded as `address`.
Hosts that cannot be reached (or where the probe cannot run) are listed in the
overview as "no report"; a per-host DB error is reported in the overview's
Status column instead of dropping the host.

Output (under `reports/`, git-ignored):

- `db_probe_<instance>_<stamp>.json` — one per probed server: every table with
  rows/data/index bytes, per-database totals and the newest backup set. The
  `<instance>` short name is the first hostname label for hosts under `onice.io`
  (`icewarp.onice.io` -> `icewarp`); other hosts keep the full name
  (`mail.icewarp.com`).
- `db_probe_overview_<stamp>.json` / `.md` — the combined overview. It ranks the
  probed instances **by per-database top row count** (the largest table in each
  IceWarp database) and **by per-database top data size**, and lists each
  instance's latest backup set. Full per-table detail stays in the per-server
  JSON.



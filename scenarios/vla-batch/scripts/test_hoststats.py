"""Tests voor hoststats.py: python -m pytest scripts/test_hoststats.py"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hoststats as H  # noqa: E402


def test_cpu_pct_from_two_proc_stat_samples():
    prev = H.cpu_times("cpu  100 0 50 800 10 0 0 40 0 0\n")
    cur = H.cpu_times("cpu  160 0 70 860 20 5 5 80 0 0\n")
    # delta: user 60, system 20, idle 60, iowait 10, irq 5, softirq 5, steal 40 -> totaal 200
    got = H.cpu_pct(prev, cur)
    assert got == {"cpu_user": 30.0, "cpu_system": 15.0, "cpu_idle": 30.0,
                   "cpu_iowait": 5.0, "cpu_steal": 20.0}


def test_cpu_pct_without_previous_sample_is_null_not_zero():
    assert set(H.cpu_pct(None, [1] * 8).values()) == {None}


def test_meminfo_and_disk():
    m = H.meminfo("MemTotal: 8126464 kB\nMemAvailable: 4096000 kB\n"
                  "SwapTotal: 4194304 kB\nSwapFree: 4161536 kB\n")
    assert m == {"mem_total_mb": 7936.0, "mem_avail_mb": 4000.0, "swap_used_mb": 32.0}

    class St:  # 100 blokken, 30 vrij waarvan 20 voor gewone gebruikers
        f_blocks, f_bfree, f_bavail, f_frsize = 100, 30, 20, 1024 ** 3
    assert H.disk(St) == {"disk_used_pct": 77.78, "disk_free_gb": 20.0}


def test_containers_merges_stats_and_inspect_and_keeps_stopped_ones():
    stats = [{"Name": "vla-tdengine", "CPUPerc": "3.5%", "MemUsage": "480MiB / 768MiB",
              "MemPerc": "62.5%"}]
    inspect = ["/vla-tdengine|2|healthy|true|805306368",
               "/emcp-embeddings|0||false|0",
               "/vla-gone|1||false|0"]
    rows = {r["Name"]: r for r in H.containers(stats, inspect)}
    td = rows["vla-tdengine"]
    assert (td["cpu_pct"], td["mem_mb"], td["mem_limit_mb"], td["restarts"], td["health"]) == \
        (3.5, 480.0, 768.0, 2, "healthy")
    assert rows["emcp-embeddings"]["running"] is False
    assert H.summary(list(rows.values())) == {
        "containers_running": 1, "containers_unhealthy": 0,
        "containers_stopped_unexpected": 1}   # emcp-embeddings staat bewust uit, vla-gone niet


def test_insert_sql_quotes_and_nulls():
    sql = H.insert_sql(1700000000000, "srv-1", {"load1": 1.5, "cpu_steal": None},
                       [{"Name": "a'b", "cpu_pct": 1.0, "running": True, "health": "unhealthy"}],
                       {"x.example": 12.5})
    assert sql.startswith("INSERT INTO idp.vps_h_srv_1 USING idp.vps_host TAGS ('srv-1') "
                          "VALUES (1700000000000, 1.5, NULL")
    assert "idp.vps_c_a_b USING idp.vps_container TAGS ('a\\'b') VALUES (1700000000000, 1.0, " \
           "NULL, NULL, NULL, NULL, 0, 1)" in sql
    assert "idp.vps_cert_x_example USING idp.vps_cert TAGS ('x.example') " \
           "VALUES (1700000000000, 12.5)" in sql
    assert sql.count("VALUES") == 3
    stopped = H.insert_sql(1, "h", {}, [{"Name": "x", "running": False, "health": "unhealthy"}], {})
    assert stopped.endswith("NULL, 0)")   # gestopt: health leeg, niet "unhealthy"
    assert len(H.HOST_COLS) + 1 == H.SCHEMA[0].count(" DOUBLE") + H.SCHEMA[0].count(" INT") + 1


def test_env_file_points_at_stack_root():
    assert H.ENV_FILE.parent.name == "idp-os" or H.ENV_FILE.parent == \
        Path(__file__).resolve().parents[3]
    assert os.path.basename(H.ENV_FILE) == ".env"


def test_backfill_parses_sadf_and_skips_restart_lines():
    import hoststats_backfill as B
    u = ("# hostname;interval;timestamp;CPU;%usr;%nice;%sys;%iowait;%steal;%irq;%soft;%guest;%gnice;%idle\n"
         "srv;120;1000;-1;14.19;0.00;6.72;0.37;13.61;0.00;0.37;0.00;0.00;64.74\n"
         "srv;-1;1100;LINUX-RESTART (2 CPU)\n")
    q = ("# hostname;interval;timestamp;runq-sz;plist-sz;ldavg-1;ldavg-5;ldavg-15;blocked\n"
         "srv;120;1000;3;1562;1.40;1.39;1.18;0\n")
    merged = {}
    for txt in (u, q):
        for ts, v in B.parse_sadf(txt).items():
            merged.setdefault(ts, {}).update(v)
    assert list(merged) == [1000]
    row = B.to_host_row(merged[1000], 7936.0)
    assert row["cpu_user"] == 14.19 and row["cpu_system"] == 7.09 and row["cpu_steal"] == 13.61
    assert row["load1"] == 1.4 and row["mem_avail_mb"] is None    # geen -r: leeg, niet verzonnen
    stmts = B.insert_batches([(1000, row)], "srv")
    assert stmts[0].startswith("INSERT INTO idp.vps_h_srv USING idp.vps_host TAGS ('srv') VALUES (1000000, 1.4")

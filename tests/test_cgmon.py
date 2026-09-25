import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import cgmon  # noqa: E402

CGMON = os.path.join(REPO, "cgmon.py")


def run_cgmon(*args):
    env = dict(os.environ, NO_COLOR="1")
    return subprocess.run([sys.executable, CGMON] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, env=env, timeout=30)


class TestFormatters(unittest.TestCase):
    def test_format_usec(self):
        self.assertEqual(cgmon.format_usec("0"), "0us")
        self.assertEqual(cgmon.format_usec("999"), "999us")
        self.assertEqual(cgmon.format_usec("1000"), "1ms")
        self.assertEqual(cgmon.format_usec("1500"), "1.5ms")
        self.assertEqual(cgmon.format_usec("15000"), "15ms")
        self.assertEqual(cgmon.format_usec("2500000"), "2.5s")
        self.assertEqual(cgmon.format_usec("90000000"), "90.0s")
        self.assertEqual(cgmon.format_usec("640000000"), "640s")
        self.assertEqual(cgmon.format_usec("max"), "max")
        self.assertEqual(cgmon.format_usec("junk"), "junk")
        self.assertEqual(cgmon.format_usec(1500), "1.5ms")

    def test_format_bytes(self):
        self.assertEqual(cgmon.format_bytes("512"), "512B")
        self.assertEqual(cgmon.format_bytes("1024"), "1K")
        self.assertEqual(cgmon.format_bytes("1536"), "1.5K")
        self.assertEqual(cgmon.format_bytes(str(150 * 1024**2)), "150M")
        self.assertEqual(cgmon.format_bytes(str(3 * 1024**3)), "3G")
        self.assertEqual(cgmon.format_bytes(str(2 * 1024**4)), "2T")
        self.assertEqual(cgmon.format_bytes("max"), "max")

    def test_format_count(self):
        self.assertEqual(cgmon.format_count("999"), "999")
        self.assertEqual(cgmon.format_count("1000"), "1K")
        self.assertEqual(cgmon.format_count("1500"), "1.5K")
        self.assertEqual(cgmon.format_count("2000000"), "2M")
        self.assertEqual(cgmon.format_count("max"), "max")

    def test_missing_values_render_as_dash(self):
        for fmt in (cgmon.format_usec, cgmon.format_bytes, cgmon.format_count,
                    cgmon.format_pct, cgmon.format_cpu_max):
            self.assertEqual(fmt(None), "-", fmt.__name__)
        self.assertEqual(cgmon.format_pct(12.345), "12.3")


class TestParsers(unittest.TestCase):
    def test_parse_kv(self):
        self.assertIsNone(cgmon.parse_kv(None))
        self.assertEqual(cgmon.parse_kv(""), {})
        self.assertEqual(cgmon.parse_kv("usage_usec 10\nuser_usec 7\nbogus\n"),
                         {"usage_usec": "10", "user_usec": "7"})

    def test_parse_cpu_max(self):
        self.assertIsNone(cgmon.parse_cpu_max(None))
        self.assertEqual(cgmon.parse_cpu_max(""), "max")
        self.assertEqual(cgmon.parse_cpu_max("max 100000"), "max")
        self.assertEqual(cgmon.parse_cpu_max("50000 100000"), "0.5c")
        self.assertEqual(cgmon.parse_cpu_max("200000 100000"), "2c")
        self.assertEqual(cgmon.parse_cpu_max("50000 0"), "50000")

    def test_parse_pressure(self):
        content = ("some avg10=0.00 avg60=0.00 avg300=0.00 total=12345\n"
                   "full avg10=0.00 avg60=0.00 avg300=0.00 total=999")
        self.assertEqual(cgmon.parse_pressure(content), "12345")
        self.assertIsNone(cgmon.parse_pressure(None))
        self.assertIsNone(cgmon.parse_pressure(""))

    def test_parse_io_stat_sums_devices(self):
        content = ("8:0 rbytes=100 wbytes=200 rios=1 wios=2 dbytes=0 dios=0\n"
                   "8:16 rbytes=10 wbytes=20 rios=3 wios=4 dbytes=0 dios=0")
        self.assertEqual(cgmon.parse_io_stat(content),
                         {"rbytes": 110, "wbytes": 220, "rios": 4, "wios": 6})
        # Empty io.stat means no I/O yet (real zeros); a missing file means unknown
        self.assertEqual(cgmon.parse_io_stat(""),
                         {"rbytes": 0, "wbytes": 0, "rios": 0, "wios": 0})
        self.assertIsNone(cgmon.parse_io_stat(None))

    def test_parse_io_weight(self):
        self.assertIsNone(cgmon.parse_io_weight(None))
        self.assertIsNone(cgmon.parse_io_weight(""))
        self.assertEqual(cgmon.parse_io_weight("default 250\n8:0 500"), "250")
        self.assertEqual(cgmon.parse_io_weight("8:0 500\ndefault 250"), "250")
        self.assertEqual(cgmon.parse_io_weight("300"), "300")

    def test_parse_proc_cgroup(self):
        self.assertEqual(cgmon.parse_proc_cgroup("0::/system.slice/docker-abc.scope\n"),
                         "system.slice/docker-abc.scope")
        self.assertEqual(cgmon.parse_proc_cgroup("0::/weird:name/child\n"), "weird:name/child")
        self.assertEqual(cgmon.parse_proc_cgroup("0::/\n"), "")
        self.assertIsNone(cgmon.parse_proc_cgroup("12:memory:/foo\n1:cpu:/foo\n"))


class TestFileCache(unittest.TestCase):
    def tearDown(self):
        cgmon.close_file_cache()

    def test_missing_file_is_evicted_and_reopened(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "pids.current")
            with open(path, "w") as f:
                f.write("3\n")
            self.assertEqual(cgmon.read_cgroup_file(d, "pids.current"), "3")
            self.assertIn(path, cgmon._FILE_CACHE)
            cgmon.close_file_cache()
            self.assertEqual(cgmon._FILE_CACHE, {})
            self.assertIsNone(cgmon.read_cgroup_file(d, "nope"))
            self.assertNotIn(os.path.join(d, "nope"), cgmon._FILE_CACHE)


class TestComputeRow(unittest.TestCase):
    PREV = {
        'cpu': {'usage': '1000000', 'usr': '600000', 'sys': '400000', 'nr_periods': '100',
                'nr_thr': '10', 'thr_us': '5000', 'max': '0.5c', 'psi': '0'},
        'memory': {'cur': '100', 'anon': '50', 'file': '50', 'mjflt': '3', 'oom': '1',
                   'max': 'max', 'psi': '0'},
    }

    def test_cpu_rates_and_deltas(self):
        curr = {'cpu': {'usage': '2000000', 'usr': '1500000', 'sys': '500000',
                        'nr_periods': '120', 'nr_thr': '15', 'thr_us': '9000',
                        'max': '0.5c', 'psi': '2500'}}
        vals = cgmon.compute_row(self.PREV, curr, 2.0)['cpu']
        self.assertAlmostEqual(vals['%cpu'], 50.0)   # 1s of CPU over 2s of wall time
        self.assertAlmostEqual(vals['%usr'], 45.0)
        self.assertAlmostEqual(vals['%sys'], 5.0)
        self.assertEqual(vals['nr_thr'], 5)
        self.assertAlmostEqual(vals['thr%'], 25.0)   # 5 of 20 periods throttled
        self.assertEqual(vals['thr_us'], 4000)
        self.assertEqual(vals['psi'], 2500)
        self.assertEqual(vals['max'], '0.5c')         # gauge passed through

    def test_no_periods_means_no_throttling(self):
        curr = {'cpu': dict(self.PREV['cpu'])}
        self.assertEqual(cgmon.compute_row(self.PREV, curr, 1.0)['cpu']['thr%'], 0.0)

    def test_counter_reset_counts_from_zero(self):
        curr = {'memory': dict(self.PREV['memory'], mjflt='2', oom='0', cur='80')}
        vals = cgmon.compute_row(self.PREV, curr, 1.0)['memory']
        self.assertEqual(vals['mjflt'], 2)
        self.assertEqual(vals['oom'], 0)
        self.assertEqual(vals['cur'], '80')

    def test_missing_values_stay_missing(self):
        prev = {'io': {'rbytes': None, 'wbytes': None, 'rios': None, 'wios': None,
                       'weight': None, 'psi': '10'}}
        curr = {'io': dict(prev['io'], psi='30')}
        vals = cgmon.compute_row(prev, curr, 1.0)['io']
        self.assertIsNone(vals['rbytes'])
        self.assertIsNone(vals['weight'])
        self.assertEqual(vals['psi'], 20)
        # Stats missing entirely (e.g. no cpu.stat) must not become 0%
        cpu_curr = {'cpu': {k: None for k in self.PREV['cpu']}}
        self.assertIsNone(cgmon.compute_row({}, cpu_curr, 1.0)['cpu']['%cpu'])


FAKE_CGROUP = {
    "cgroup.controllers": "cpu memory pids\n",
    "cpu.stat": "usage_usec 1000\nuser_usec 600\nsystem_usec 400\nnr_throttled 0\nthrottled_usec 0\n",
    "cpu.max": "50000 100000\n",
    "cpu.pressure": "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n",
    "memory.current": str(100 * 1024**2) + "\n",
    "memory.stat": "anon 1024\nfile 2048\npgmajfault 0\n",
    "memory.events": "oom 0\noom_kill 0\n",
    "memory.max": str(150 * 1024**2) + "\n",
    "memory.pressure": "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n",
    "pids.current": "7\n",
    "pids.max": "max\n",
}


def make_fake_cgroup(d):
    for name, content in FAKE_CGROUP.items():
        with open(os.path.join(d, name), "w") as f:
            f.write(content)


class TestCli(unittest.TestCase):
    def test_fake_cgroup_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            make_fake_cgroup(d)
            r = run_cgmon("-c", d, "-m", "cpu,memory,pids", "0.1", "2")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(len(lines), 4, r.stdout)
        self.assertIn("-cpu-", lines[0])
        self.assertIn("-pids-", lines[0])
        for row in lines[2:]:
            fields = row.split()
            self.assertIn("0.5c", fields)   # cpu.max gauge
            self.assertIn("100M", fields)   # memory.current gauge
            self.assertIn("150M", fields)   # memory.max gauge
            self.assertEqual(fields[-2:], ["7", "max"])  # pids cur/max
            self.assertEqual(fields[1], "0.0")          # %cpu: static counters
        self.assertEqual(r.stderr, "")

    def test_disabled_controller_shows_dash_and_warns(self):
        with tempfile.TemporaryDirectory() as d:
            make_fake_cgroup(d)
            r = run_cgmon("-c", d, "-m", "io", "0.1", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("io controller is not enabled", r.stderr)
        fields = r.stdout.splitlines()[2].split()
        self.assertEqual(fields[1:6], ["-"] * 5)       # no io.stat / io.weight

    def test_unknown_metric_is_rejected(self):
        r = run_cgmon("-m", "cpu,mem", "1", "1")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        self.assertIn("unknown metrics: mem", r.stderr)

    def test_non_cgroup_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            r = run_cgmon("-c", d, "0.1", "1")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertIn("not a cgroups v2 directory", r.stderr)

    def test_deleted_cgroup_exits_cleanly(self):
        d = tempfile.mkdtemp()
        try:
            make_fake_cgroup(d)
            env = dict(os.environ, NO_COLOR="1")
            p = subprocess.Popen([sys.executable, CGMON, "-c", d, "0.1", "100"],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 universal_newlines=True, env=env)
            time.sleep(0.5)
        finally:
            shutil.rmtree(d)
        out, err = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 0, err)
        self.assertIn("Target cgroup deleted", err)

    def test_argument_errors(self):
        for args, msg in [(["1", "0"], "count"),
                          (["0"], "interval"),
                          (["-p", "0"], "PID")]:
            r = run_cgmon(*args)
            self.assertEqual(r.returncode, 2, args)
            self.assertEqual(r.stdout, "", args)
            self.assertIn(msg, r.stderr, args)

    def test_missing_cgroup_path(self):
        r = run_cgmon("-c", "/nonexistent/cgroup", "1", "1")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertIn("does not exist", r.stderr)


if __name__ == "__main__":
    unittest.main()

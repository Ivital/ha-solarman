"""Every returned read-only Modbus bit/word and raw frame is archived."""
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy" / "rakurs" / "standalone"))
from modbus_archive import PayloadError, normalize, persist
from seed_two_parallel_demo import check_source, build_payload, DEVICE_KEYS
from modbus_wire import crc16


def rtu(hex_data):
    raw = bytes.fromhex(hex_data)
    return (raw + crc16(raw).to_bytes(2, "little")).hex()



class ProtocolValidationTests(unittest.TestCase):
    def setUp(self):
        self.poll = {
            "device_key": "deye_unit_one",
            "poll_uid": "read_20261124_000001",
            "quality": "good", "ingest_source": "verified_gateway",
            "transport": "rs485", "slave_id": 1,
            "observed_at_utc": "2026-11-24T21:00:00Z",
            "frames": [
                {"function_code": 3, "start_address": 65000, "requested_count": 2,
                 "words": [0, 65535], "status": "ok",
                 "request_hex": rtu("0103fde80002"), "response_hex": rtu("0103040000ffff")},
                {"function_code": 2, "start_address": 100, "requested_count": 3,
                 "bits": [1, 0, 1], "status": "ok",
                 "request_hex": rtu("010200640003"), "response_hex": rtu("01020105")},
            ],
        }

    def test_unknown_registers_and_discrete_bits_are_not_filtered_by_catalog(self):
        parsed = normalize(self.poll)
        self.assertEqual(parsed["frames"][0]["start_address"], 65000)
        self.assertEqual(parsed["frames"][0]["words"], [0, 65535])
        self.assertEqual(parsed["frames"][1]["bits"], [1, 0, 1])
        self.assertEqual(parsed["frames"][0]["response_bytes"], bytes.fromhex(rtu("0103040000ffff")))

    def test_modbus_writes_are_not_accepted(self):
        for fc in [5, 6, 15, 16]:
            packet = {**self.poll, "frames": [
                {**self.poll["frames"][0], "function_code": fc}
            ]}
            with self.assertRaises(PayloadError):
                normalize(packet)

    def test_partial_response_is_rejected_to_prevent_silent_loss(self):
        p = dict(self.poll)
        p["frames"] = [{**p["frames"][0], "words": [22]}]
        with self.assertRaises(PayloadError):
            normalize(p)

    def test_real_frames_require_wire_bytes(self):
        p = dict(self.poll)
        p["frames"] = [{**p["frames"][0], "response_hex": None}]
        with self.assertRaises(PayloadError):
            normalize(p)

    def test_error_frame_retained_without_invented_values(self):
        p = dict(self.poll)
        p["frames"] = [
            {"function_code": 3, "start_address": 33, "requested_count": 30,
             "status": "timeout", "words": [], "error_code": "READ_TIMEOUT",
             "request_hex": rtu("01030021001e")},
        ]
        self.assertEqual(normalize(p)["frames"][0]["status"], "timeout")
        p["frames"][0]["words"] = [10] * 30
        with self.assertRaises(PayloadError):
            normalize(p)

    def test_simulated_frames_must_be_unambiguously_marked(self):
        p = dict(self.poll, quality="simulated", transport="simulation",
                 slave_id=None)
        p["frames"] = [{
            "function_code": 3, "start_address": 0,
            "requested_count": 1, "words": [0], "status": "simulated",
        }]
        self.assertEqual(normalize(p)["frames"][0]["words"], [0])
        with self.assertRaises(PayloadError):
            normalize({**p, "quality": "good"})


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.lastrowid = None
        self.last = None
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def execute(self, sql, params=()):
        self.db.statements.append((sql, params))
        self.last = sql
        if sql.startswith("INSERT "):
            self.db.counter += 1
            self.lastrowid = self.db.counter
    def executemany(self, sql, rows):
        rows = list(rows)
        self.db.insert_many.append((sql, rows))
    def fetchone(self):
        if "FROM rakurs_devices WHERE device_key" in self.last:
            return (11, self.db.kind)
        if "FROM rakurs_parallel_members" in self.last:
            return (1,) if self.db.verified else None
        if "FROM rakurs_modbus_poll_runs WHERE poll_uid" in self.last:
            return None
        if "FROM rakurs_modbus_fields WHERE field_key" in self.last:
            return (12,)
        if "FROM rakurs_signals WHERE signal_key" in self.last:
            return (13,)
        return None


class FakeDb:
    def __init__(self, kind="simulation", verified=False):
        self.kind = kind
        self.verified = verified
        self.statements = []
        self.insert_many = []
        self.counter = 100
        self.began = 0
        self.committed = 0
        self.rolled_back = 0
    def begin(self):
        self.began += 1
    def cursor(self):
        return FakeCursor(self)
    def commit(self):
        self.committed += 1
    def rollback(self):
        self.rolled_back += 1


class ArchiveTransactionTests(unittest.TestCase):
    def test_simulated_raw_words_fields_and_signals_saved_atomically(self):
        payload = {
            "device_key": DEVICE_KEYS[0], "poll_uid": "simulated_unit1_v1",
            "quality": "simulated", "transport": "simulation",
            "slave_id": None, "ingest_source": "simulated_test",
            "observed_at_utc": "2026-11-24T21:00:00Z",
            "frames": [
                {"function_code": 3, "start_address": 1,
                 "requested_count": 2, "words": [1, 2]},
                {"function_code": 3, "start_address": 1,
                 "requested_count": 2, "words": [3, 4]},
            ],
            "fields": [{"field_key": "p3_0001", "value_decimal": "10",
                        "raw_registers": {"1": 1}}],
            "signals": [{"signal_key": "pv_power", "value_decimal": "5.1"}],
        }
        db = FakeDb()
        receipt = persist(db, payload)
        self.assertEqual(receipt["raw_registers"], 4)
        self.assertEqual(receipt["decoded_fields"], 1)
        self.assertEqual(db.began, 1)
        self.assertEqual(db.committed, 1)
        self.assertEqual(db.rolled_back, 0)
        self.assertEqual(len(db.insert_many), 2)
        self.assertEqual(sum(len(r) for _, r in db.insert_many), 4)
        self.assertEqual(len([q for q, _ in db.statements
                              if q.startswith("INSERT INTO rakurs_modbus_frames")]), 2)

    def test_live_requires_verified_member(self):
        db = FakeDb(kind="inverter", verified=False)
        payload = ProtocolValidationTests().setUp  # no payload from a physical device
        template = {
            "device_key": "deye_one", "poll_uid": "real_poll_0001",
            "quality": "good", "transport": "rs485", "slave_id": 1,
            "ingest_source": "real_collector",
            "observed_at_utc": "2026-11-24T21:00:00Z",
            "frames": [{"function_code": 3, "start_address": 800,
                        "requested_count": 1, "words": [190],
                        "request_hex": rtu("010303200001"),
                        "response_hex": rtu("01030200be")}],
        }
        with self.assertRaises(PayloadError):
            persist(db, template)
        self.assertEqual(db.committed, 0)
        self.assertEqual(db.rolled_back, 1)

    def test_verified_real_timeout_writes_device_event_without_fake_samples(self):
        db = FakeDb(kind="inverter", verified=True)
        payload = {
            "device_key": "deye_unit_1", "poll_uid": "timeout_poll_2026_01",
            "quality": "partial", "transport": "rs485", "slave_id": 1,
            "ingest_source": "approved_collector",
            "observed_at_utc": "2026-11-25T00:00:00Z",
            "frames": [{
                "function_code": 3, "start_address": 100, "requested_count": 2,
                "status": "timeout", "words": [], "bits": [],
                "request_hex": rtu("010300640002"),
                "error_code": "READ_TIMEOUT",
            }],
        }
        receipt = persist(db, payload)
        self.assertEqual(receipt["raw_registers"], 0)
        self.assertTrue(any(
            q.startswith("INSERT INTO rakurs_device_events") and
            params[3] == "MODBUS_TIMEOUT"
            for q, params in db.statements
        ))
        self.assertEqual(db.committed, 1)

    def test_source_contains_complete_two_device_snapshot(self):
        fields, signals, addresses = check_source()
        self.assertEqual((len(fields), len(signals), len(addresses)), (624, 316, 815))
        t = datetime(2026, 11, 24, 21, tzinfo=timezone.utc)
        a = build_payload(1, fields, signals, addresses, t)
        b = build_payload(2, fields, signals, addresses, t)
        self.assertEqual((len(a["fields"]), len(b["fields"])), (624, 624))
        self.assertEqual((len(a["signals"]), len(b["signals"])), (316, 316))
        self.assertEqual((sum(len(f["words"]) for f in a["frames"]),
                          sum(len(f["words"]) for f in b["frames"])), (815, 815))
        self.assertNotEqual(a["frames"][0]["words"], b["frames"][0]["words"])
        self.assertTrue(all(not frame.get("request_hex") for frame in a["frames"]))


class ParallelSchemaMigrationTests(unittest.TestCase):
    """Regression for the first deploy failure: SQL comments contain ';'."""

    def test_real_schema_contains_six_only_allowed_create_statements(self):
        from seed_two_parallel_demo import SCHEMA, SCHEMA_TABLES, parse_schema_ddl
        sql = SCHEMA.read_text(encoding="utf-8")
        self.assertIn("tables; NO claims", sql)
        statements = parse_schema_ddl(sql)
        self.assertEqual(len(statements), 6)
        for name, statement in zip(SCHEMA_TABLES, statements):
            self.assertTrue(statement.startswith(
                "CREATE TABLE IF NOT EXISTS " + name + " ("))
            self.assertNotIn("--", statement)
        self.assertTrue(statements[-1].endswith(
            "COLLATE=utf8mb4_unicode_ci"))

    def test_parser_ignores_semicolons_in_sql_comment_lines(self):
        from seed_two_parallel_demo import SCHEMA, parse_schema_ddl
        sql = SCHEMA.read_text(encoding="utf-8")
        commented = "-- comment with ; separators ; DROP DATABASE rakurs;\n" + sql
        self.assertEqual(parse_schema_ddl(commented), parse_schema_ddl(sql))

    def test_invalid_schema_is_rejected_before_executing_first_table(self):
        from unittest.mock import patch
        from seed_two_parallel_demo import apply_schema, SCHEMA
        text = SCHEMA.read_text(encoding="utf-8")
        text += "\nDROP TABLE rakurs_devices;\n"

        class CaptureCursor:
            def __init__(self):
                self.writes = []
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def execute(self, sql):
                self.writes.append(sql)

        class FakeDb:
            def __init__(self):
                self.cursor_instance = CaptureCursor()
            def cursor(self):
                return self.cursor_instance

        db = FakeDb()
        with patch("seed_two_parallel_demo.SCHEMA") as mock_schema:
            mock_schema.read_text.return_value = text
            with self.assertRaisesRegex(RuntimeError, "unsafe DDL"):
                apply_schema(db)
        self.assertEqual(db.cursor_instance.writes, [])

    def test_all_six_tables_execute_in_dependency_order(self):
        from seed_two_parallel_demo import apply_schema, SCHEMA_TABLES

        class CaptureCursor:
            def __init__(self):
                self.writes = []
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def execute(self, sql):
                self.writes.append(sql)

        class FakeDb:
            def __init__(self):
                self.cursor_instance = CaptureCursor()
            def cursor(self):
                return self.cursor_instance

        db = FakeDb()
        apply_schema(db)
        self.assertEqual(len(db.cursor_instance.writes), 6)
        for name, statement in zip(SCHEMA_TABLES, db.cursor_instance.writes):
            self.assertTrue(statement.startswith(
                "CREATE TABLE IF NOT EXISTS " + name + " ("))


class BackupTlsTests(unittest.TestCase):
    """Self-signed MySQL TLS must never be bypassed implicitly."""

    def test_mysql_verified_by_default_and_plaintext_requires_explicit_ack(self):
        from unittest.mock import patch
        import subprocess
        from seed_two_parallel_demo import mysql_dump_tls_flags
        binary = "/usr/bin/mysqldump"
        result = subprocess.CompletedProcess(
            [binary, "--version"], 0,
            stdout=b"mysqldump  Ver 8.0.40 for Linux", stderr=b"")
        with patch("seed_two_parallel_demo.subprocess.run", return_value=result):
            self.assertEqual(
                mysql_dump_tls_flags(binary, "VERIFY_TLS"),
                ["--ssl-mode=VERIFY_IDENTITY"])
            self.assertEqual(
                mysql_dump_tls_flags(binary, "LAN_PLAINTEXT_ACK"),
                ["--ssl-mode=DISABLED"])
            with self.assertRaises(RuntimeError):
                mysql_dump_tls_flags(binary, "DISABLED")

    def test_mariadb_client_uses_compatible_switches(self):
        from unittest.mock import patch
        import subprocess
        from seed_two_parallel_demo import mysql_dump_tls_flags
        binary = "/usr/bin/mariadb-dump"
        result = subprocess.CompletedProcess(
            [binary, "--version"], 0,
            stdout=b"mariadb-dump Ver 10.19 Distrib 10.11.14-MariaDB", stderr=b"")
        with patch("seed_two_parallel_demo.subprocess.run", return_value=result):
            self.assertEqual(
                mysql_dump_tls_flags(binary, "VERIFY_TLS"),
                ["--ssl", "--ssl-verify-server-cert"])
            self.assertEqual(
                mysql_dump_tls_flags(binary, "LAN_PLAINTEXT_ACK"),
                ["--skip-ssl"])

    def test_mysqldump_stdout_is_actually_gzipped_and_verified(self):
        import gzip
        import sys
        import tempfile
        from seed_two_parallel_demo import stream_dump_to_gzip
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "rakurs.sql.gz.partial"
            command = [sys.executable, "-c",
                       "import sys;sys.stdout.write('CREATE TABLE rakurs_test (id INT);\\n' * 4096)"]
            code, error = stream_dump_to_gzip(command, output, timeout_seconds=20)
            self.assertEqual(code, 0, error)
            self.assertEqual(output.read_bytes()[:2], bytes.fromhex("1f8b"))
            with gzip.open(output, "rb") as handle:
                plain = handle.read()
            self.assertEqual(plain.count(b"CREATE TABLE rakurs_test"), 4096)
            self.assertLess(output.stat().st_size, len(plain) // 2)

    def test_backup_subprocess_failure_does_not_pretend_success(self):
        import sys
        import tempfile
        from seed_two_parallel_demo import stream_dump_to_gzip
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "incomplete.sql.gz.partial"
            code, stderr = stream_dump_to_gzip(
                [sys.executable, "-c",
                 "import sys;sys.stdout.write('partial');sys.exit(12)"],
                output, timeout_seconds=20
            )
            self.assertEqual(code, 12)
            self.assertFalse((Path(tmp) / "incomplete.sql.gz").exists())

    def test_trusted_ca_requires_real_protected_file(self):
        from unittest.mock import patch
        import subprocess
        from seed_two_parallel_demo import mysql_dump_tls_flags
        binary = "/usr/bin/mysqldump"
        result = subprocess.CompletedProcess(
            [binary, "--version"], 0, stdout=b"mysqldump Ver 8.0", stderr=b"")
        with patch("seed_two_parallel_demo.subprocess.run", return_value=result):
            with self.assertRaises(RuntimeError):
                mysql_dump_tls_flags(binary, "VERIFY_TLS",
                                     "/nonexistent/mysql-ca-do-not-trust.pem")


class ResumeAfterSuccessfulTwinSeedTests(unittest.TestCase):
    """Regress private-home audit permission and duplicate writes on resume."""

    def test_resume_skips_all_mysql_ddl_dml_and_owner_password(self):
        source = (ROOT / "deploy/rakurs/standalone/"
                  "deploy_full_modbus_archive.sh").read_text()
        self.assertIn('MODE="${1:---check}"', source)
        self.assertIn('"$MODE" == "--resume"', source)
        self.assertIn('if [[ "$MODE" == "--apply" ]]; then', source)
        self.assertIn("RESUME=SKIP_SEED_AND_BACKUP", source)
        self.assertIn("RESUME_DB_WRITES=0", source)
        # Under --resume the conditional backup/seed is bypassed.
        i = source.index('if [[ "$MODE" == "--apply" ]]; then\n  echo')
        j = source.index("else\n  # Seed already committed", i)
        seed = source.index("seed_two_parallel_demo.py\" --execute", i)
        self.assertLess(seed, j)
        self.assertFalse("seed_two_parallel_demo.py\" --execute" in source[j:])

    def test_private_checkout_read_via_stdin_not_by_restricted_user(self):
        source = (ROOT / "deploy/rakurs/standalone/"
                  "deploy_full_modbus_archive.sh").read_text()
        self.assertIn(
            'cat "$HERE/audit_two_inverters_mysql.py" | sudo -u rakurs env',
            source,
        )
        self.assertIn('/var/lib/rakurs/.venv/bin/python -', source)
        self.assertNotIn(
            '/var/lib/rakurs/.venv/bin/python "$HERE/audit_two_inverters_mysql.py"',
            source,
        )
        self.assertNotIn("chmod 755 /home/ivital", source)
        self.assertLess(
            source.index("TWIN_DATABASE_AUDIT_BEFORE_DEPLOY=PASS"),
            source.index('bash "$HERE/deploy_parallel_ui.sh" --apply'),
        )

    def test_shell_script_parses(self):
        import subprocess
        script = ROOT / "deploy/rakurs/standalone/deploy_full_modbus_archive.sh"
        subprocess.run(["bash", "-n", str(script)], check=True)


if __name__ == "__main__":
    unittest.main()

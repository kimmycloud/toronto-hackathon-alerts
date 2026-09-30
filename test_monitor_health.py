import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import monitor_health as health
import discover


class MonitorHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "health.json"
        self.state_patch = patch.object(health, "STATE_FILE", self.state)
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)

    def test_baseline_unchanged_change_recovery_and_failed_delivery(self):
        blocked = {"name": "U of T — UofTHacks", "status": "BLOCKED", "error": "HTTP 403"}
        failed = {"name": "TMU — Example", "status": "FAILED", "error": "parser error"}
        with patch.object(health, "send_discord") as send:
            health.compare_health("annual-organizer", [blocked])
            self.assertEqual(send.call_count, 0)
            health.compare_health("annual-organizer", [{**blocked, "error": "HTTP 403 again"}])
            self.assertEqual(send.call_count, 0)
            send.side_effect = RuntimeError("delivery failed")
            with self.assertRaises(RuntimeError):
                health.compare_health("annual-organizer", [blocked, failed])
            self.assertEqual(len(json.loads(self.state.read_text())["annual-organizer"]), 1)
            send.side_effect = None
            health.compare_health("annual-organizer", [blocked, failed])
            self.assertIn("FAILED", send.call_args.args[0])
            health.compare_health("annual-organizer", [failed])
            self.assertIn("RECOVERED", send.call_args.args[0])
            self.assertEqual(send.call_count, 3)

    def test_log_parsing_and_skipped_coverage(self):
        log = Path(self.temp.name) / "club.log"
        log.write_text("Loaded 2 club discovery entries.\n"
                       "[1/2] U of T — UofTHacks\nBLOCKED: HTTP 403\n"
                       "[2/2] TMU — UX\n  URL BLOCKED: HTTP 406\nUNCHANGED: 2 signals.\n"
                       "Finished: done\n")
        with patch.object(health, "send_discord") as send:
            health.log_health(log, "club-discovery", __import__("re").compile(r"^\[\d+/\d+\] (.+?) — (.+)$"))
            self.assertEqual(len(json.loads(self.state.read_text())["club-discovery"]), 1)
            log.write_text(log.read_text().replace("BLOCKED: HTTP 403", "SKIPPED: no URL"))
            health.log_health(log, "club-discovery", __import__("re").compile(r"^\[\d+/\d+\] (.+?) — (.+)$"))
            self.assertEqual(send.call_count, 0)

    def test_university_json_and_incomplete_log(self):
        result = Path(self.temp.name) / "university.json"
        result.write_text(json.dumps({"sources": [
            {"source": "tmu", "status": "SUCCEEDED"},
            {"source": "waterloo", "status": "BLOCKED", "error": "403"}]}))
        with patch.object(health, "send_discord") as send:
            health.university_discovery_health(result)
            self.assertEqual(send.call_count, 0)
            result.write_text(json.dumps({"sources": [
                {"source": "tmu", "status": "FAILED", "error": "parse"},
                {"source": "waterloo", "status": "SUCCEEDED"}]}))
            health.university_discovery_health(result)
            self.assertIn("RECOVERED", send.call_args.args[0])
            self.assertIn("FAILED", send.call_args.args[0])
        incomplete = Path(self.temp.name) / "incomplete.log"
        incomplete.write_text("Loaded 9 annual organizer sources.\nChecking: U of T — UofTHacks\nBLOCKED: HTTP 403\n")
        with self.assertRaises(ValueError):
            health.log_health(incomplete, "annual-organizer", __import__("re").compile(r"^Checking: (.+?) — (.+)$"))

    def test_discovery_event_remains_pending_after_skipped_send(self):
        state_file = Path(self.temp.name) / "discovery.json"
        state_file.write_text(json.dumps({"initialized": True, "events": {}}))
        event = {"school": "TMU", "source": "tmu", "title": "Hackathon",
                 "url": "https://example.org/event", "start_date": "2027-01-01",
                 "end_date": "2027-01-01", "status": "upcoming"}
        with patch.object(discover, "STATE_FILE", state_file), patch.object(discover, "send_discord", return_value=False):
            discover.process_notifications([event], True)
        self.assertEqual(json.loads(state_file.read_text())["events"], {})


if __name__ == "__main__":
    unittest.main()

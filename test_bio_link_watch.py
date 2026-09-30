import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

import bio_link_watch as bio
import monitor_health as health


def page(*links):
    return "<html><body>" + "".join(f'<a href="{url}">{label}</a>' for label, url in links) + "</body></html>"


class BioLinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state_file = Path(self.temp.name) / "state.json"
        self.health_file = Path(self.temp.name) / "health.json"
        self.sources = [
            {"id": "tmu", "school": "TMU", "organization": "TMU Club", "url": "https://linktr.ee/tmu"},
            {"id": "waterloo", "school": "Waterloo", "organization": "Waterloo Club", "url": "https://linktr.ee/waterloo"},
        ]
        self.patches = [patch.object(bio, "STATE_FILE", self.state_file),
                        patch.object(bio, "load_sources", return_value=self.sources),
                        patch.object(health, "STATE_FILE", self.health_file)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def run_watch(self, pages, sender=None):
        def fetch(url):
            value = pages[url]
            if isinstance(value, Exception):
                raise value
            return value, url
        output = io.StringIO()
        with patch.object(bio, "fetch", side_effect=fetch), patch.object(bio, "send_discord", side_effect=sender) as send, contextlib.redirect_stdout(output):
            bio.main()
        return output.getvalue(), send

    def test_first_run_and_unchanged_second_run(self):
        pages = {source["url"]: page(("Hackathon signup", "https://forms.gle/one")) for source in self.sources}
        first, sent = self.run_watch(pages)
        self.assertIn("BIO-LINK BASELINE: 2 sources stored; no Discord alerts.", first)
        self.assertEqual(sent.call_count, 0)
        second, sent = self.run_watch(pages)
        self.assertIn("2 unchanged", second)
        self.assertEqual(sent.call_count, 0)

    def test_toronto_form_and_cyber_summit(self):
        pages = {source["url"]: page() for source in self.sources}
        self.run_watch(pages)
        pages[self.sources[0]["url"]] = page(("Register", "https://forms.gle/newform"),
                                               ("TMU Cyber Summit", "https://example.org/summit"),
                                               ("Details", "https://www.eventbrite.co.uk/o/example-123"))
        _, sent = self.run_watch(pages, sender=lambda *_: True)
        self.assertEqual(sent.call_count, 3)

    def test_uoft_and_york_vague_forms_are_relevant(self):
        self.sources[:] = [
            {"id": "uoft", "school": "U of T", "organization": "U of T Club", "url": "https://linktr.ee/uoft"},
            {"id": "york", "school": "York", "organization": "York Club", "url": "https://linktr.ee/york"},
        ]
        pages = {source["url"]: page() for source in self.sources}
        self.run_watch(pages)
        for source in self.sources:
            pages[source["url"]] = page(("Details", f"https://forms.gle/{source['id']}"))
        _, sent = self.run_watch(pages, sender=lambda *_: True)
        self.assertEqual(sent.call_count, 2)

    def test_waterloo_hackathon_but_not_generic_tech_registration(self):
        pages = {source["url"]: page() for source in self.sources}
        self.run_watch(pages)
        pages[self.sources[1]["url"]] = page(("Hackathon applications", "https://forms.gle/hack"),
                                               ("Tech conference registration", "https://forms.gle/conference"))
        _, sent = self.run_watch(pages, sender=lambda *_: True)
        self.assertEqual(sent.call_count, 1)
        self.assertEqual(sent.call_args.args[1]["url"], "https://forms.gle/hack")

    def test_same_url_new_translated_label_and_tracking_are_not_new(self):
        pages = {source["url"]: page() for source in self.sources}
        pages[self.sources[0]["url"]] = page(("How to Win a Hackathon", "https://forms.gle/abc?event=one&utm_source=ig#top"))
        self.run_watch(pages)
        pages[self.sources[0]["url"]] = page(("Como ganhar um hackathon", "https://FORMS.GLE/abc?utm_medium=bio&event=one#new"))
        _, sent = self.run_watch(pages, sender=lambda *_: True)
        self.assertEqual(sent.call_count, 0)
        self.assertEqual(bio.canonical_url("https://forms.gle/abc?event=two"), "https://forms.gle/abc?event=two")

    def test_past_date_is_suppressed(self):
        pages = {source["url"]: page() for source in self.sources}
        self.run_watch(pages)
        pages[self.sources[0]["url"]] = page(("Hackathon 16. Sept. 2026", "https://forms.gle/old"))
        output, sent = self.run_watch(pages, sender=lambda *_: True)
        self.assertIn("SUPPRESSED PAST", output)
        self.assertEqual(sent.call_count, 0)

    def test_blocked_and_unavailable_continue_and_keep_state(self):
        pages = {source["url"]: page() for source in self.sources}
        self.run_watch(pages)
        pages[self.sources[0]["url"]] = bio.SourceBlockedError("HTTP 406")
        pages[self.sources[1]["url"]] = bio.SourceUnavailableError("Timeout")
        output, _ = self.run_watch(pages)
        self.assertIn("BLOCKED: HTTP 406", output)
        self.assertIn("UNAVAILABLE: Timeout", output)
        self.assertEqual(len(json.loads(self.state_file.read_text())), 2)

    def test_failed_discord_delivery_keeps_target_pending(self):
        pages = {source["url"]: page() for source in self.sources}
        self.run_watch(pages)
        pages[self.sources[0]["url"]] = page(("Hackathon", "https://forms.gle/new"))
        output, _ = self.run_watch(pages, sender=RuntimeError("delivery failed"))
        self.assertIn("DISCORD FAILED", output)
        self.assertEqual(json.loads(self.state_file.read_text())["tmu"]["links"], {})
        _, sent = self.run_watch(pages, sender=lambda *_: True)
        self.assertEqual(sent.call_count, 1)

    def test_http_status_classification(self):
        for status, expected in [(403, bio.SourceBlockedError), (406, bio.SourceBlockedError),
                                 (429, bio.SourceBlockedError), (404, bio.SourceUnavailableError),
                                 (503, bio.SourceUnavailableError)]:
            response = requests.Response()
            response.status_code = status
            response.url = "https://linktr.ee/example"
            with self.subTest(status=status), patch.object(bio.requests, "get", return_value=response):
                with self.assertRaises(expected):
                    bio.fetch(response.url)

    def test_bio_link_health_baseline_change_recovery_and_failed_delivery(self):
        log = Path(self.temp.name) / "watch.log"
        def check(statuses):
            log.write_text("Loaded 2 bio-link sources.\n" +
                           "".join(f"[{i}/2] {school} — {name}\n{status}\n" for i, (school, name, status) in enumerate(statuses, 1)) +
                           "Finished: done.\n")
            health.log_health(log, "bio-link", __import__("re").compile(r"^\[\d+/\d+\] (.+?) — (.+)$"))
        with patch.object(health, "send_discord") as send:
            check([("TMU", "TMU Club", "BLOCKED: HTTP 406"), ("Waterloo", "Waterloo Club", "UNAVAILABLE: Timeout")])
            self.assertEqual(send.call_count, 0)
            check([("TMU", "TMU Club", "SKIPPED: disabled"), ("Waterloo", "Waterloo Club", "UNAVAILABLE: Timeout")])
            self.assertEqual(send.call_count, 0)
            send.side_effect = RuntimeError("delivery failed")
            with self.assertRaises(RuntimeError):
                check([("TMU", "TMU Club", "FAILED: parse"), ("Waterloo", "Waterloo Club", "UNCHANGED: 1 target")])
            self.assertEqual(len(json.loads(self.health_file.read_text())["bio-link"]), 2)
            send.side_effect = None
            check([("TMU", "TMU Club", "FAILED: parse"), ("Waterloo", "Waterloo Club", "UNCHANGED: 1 target")])
            self.assertIn("RECOVERED", send.call_args.args[0])


if __name__ == "__main__":
    unittest.main()

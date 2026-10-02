import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import club_discovery_watch as watch


SOURCE = {"id": "synthetic_club", "name": "Synthetic Club", "school": "TMU",
          "monitor_url": "https://example.org/events", "backup_urls": []}
BASE = "https://example.org/events"


def page(*events, extra=""):
    links = "".join(f'<p><a href="{url}">{label}</a> {extra}</p>'
                    for label, url in events)
    return f"<html><body>{links}</body></html>"


class DiscoveryDedupeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state_file = Path(self.temp.name) / "state.json"
        self.sent = []
        self.html = ""
        self.patches = [
            patch.object(watch, "STATE_FILE", self.state_file),
            patch.object(watch, "load_clubs", return_value=[SOURCE]),
            patch.object(watch, "get_candidate_urls", return_value=[BASE]),
            patch.object(watch, "fetch", side_effect=lambda url: (self.html, BASE)),
            patch.object(watch, "send_discord", side_effect=self.send),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def send(self, club, signals):
        self.sent.append(tuple(signals))
        return True

    def run_page(self, html):
        self.html = html
        watch.main()
        return watch.load_state()[SOURCE["id"]]

    def test_three_changing_pages_then_one_new_event(self):
        first = ("Autumn Hackathon", "/events/autumn")
        second = ("Winter Coding Competition", "/events/winter")
        third = ("Spring Case Competition", "/events/spring")
        state = self.run_page(page(first, second))  # baseline
        self.assertEqual(self.sent, [])
        self.assertEqual(len(state["seen_event_ids"]), 2)

        self.run_page(page(("Autumn Hackathon registration", "/events/autumn/?utm_source=news#apply"),
                           second, extra="Updated public details"))
        self.run_page(page(("Apply for Autumn Hackathon", "/events/autumn?fbclid=123"),
                           ("Winter Coding Competition 2026", "/events/winter"),
                           extra="More details changed again"))
        self.assertEqual(self.sent, [])

        state = self.run_page(page(first, second, third, extra="Another update"))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.sent[0]), 1)
        self.assertIn("/events/spring", self.sent[0][0])
        self.assertEqual(len(state["seen_event_ids"]), 3)

        self.run_page(page(first, second, third))
        self.assertEqual(len(self.sent), 1)

    def test_two_distinct_new_urls_alert_and_survive_reload(self):
        self.run_page("<html><body>No events yet</body></html>")
        self.run_page(page(("First Hackathon", "/events/first?event=1"),
                           ("Second Hackathon", "/events/second?event=2")))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.sent[0]), 2)
        self.assertEqual(len(watch.load_state()[SOURCE["id"]]["seen_event_ids"]), 2)
        self.run_page(page(("First Hackathon updated", "/events/first?event=1&utm_medium=email"),
                           ("Second Hackathon", "/events/second?event=2")))
        self.assertEqual(len(self.sent), 1)

    def test_failed_delivery_retries_even_when_page_unchanged(self):
        self.run_page("<html><body>No events yet</body></html>")
        candidate = page(("New Hackathon", "/events/new"))
        with patch.object(watch, "send_discord", return_value=False):
            state = self.run_page(candidate)
        self.assertEqual(state["seen_event_ids"], [])
        self.run_page(candidate)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(watch.load_state()[SOURCE["id"]]["seen_event_ids"]), 1)

    def test_legacy_snapshot_migrates_without_alert(self):
        signal = "LINK|Old Hackathon|https://example.org/events/old"
        watch.save_state({SOURCE["id"]: {"signals": [signal],
                                          "fingerprint": watch.fingerprint([signal]),
                                          "urls": [BASE]}})
        state = self.run_page(page(("Old Hackathon new label", "/events/old/")))
        self.assertEqual(self.sent, [])
        self.assertEqual(len(state["seen_event_ids"]), 1)

    def test_disappearing_event_does_not_alert_on_return(self):
        event = ("Annual Hackathon", "/events/annual")
        self.run_page("<html><body>No events yet</body></html>")
        self.run_page(page(event))
        self.assertEqual(len(self.sent), 1)
        self.run_page("<html><body>Calendar temporarily empty</body></html>")
        self.run_page(page(("Annual Hackathon registration open", "/events/annual#register")))
        self.assertEqual(len(self.sent), 1)

    def test_text_only_candidate_is_persistent(self):
        self.run_page("<html><body>No events yet</body></html>")
        self.run_page("<h2>Community Hackathon 2027</h2>")
        self.assertEqual(len(self.sent), 1)
        self.run_page("<html><body>Nothing listed</body></html>")
        self.run_page("<p>community  hackathon 2027</p>")
        self.assertEqual(len(self.sent), 1)

    def test_text_only_identity_and_distinct_query_values(self):
        signals = ["TEXT|Club Hackathon 2027", "TEXT|Club Hackathon 2028"]
        ids = watch.signal_identities(SOURCE["id"], signals)
        self.assertNotEqual(ids[signals[0]], ids[signals[1]])
        self.assertNotEqual(watch.canonical_event_url("https://example.org/register?event=1"),
                            watch.canonical_event_url("https://example.org/register?event=2"))
        self.assertIsNone(watch.canonical_event_url("javascript:alert(1)"))


if __name__ == "__main__":
    unittest.main()

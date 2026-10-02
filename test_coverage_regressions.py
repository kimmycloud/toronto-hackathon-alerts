import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup

import bio_link_watch as bio
import club_discovery_watch as clubs
import hackathon_monitor as main


class CoverageRegressions(unittest.TestCase):
    def test_next_season_mlh_ottawa_and_existing_toronto(self):
        html = """
        <a href="https://2027.uottahack.ca/">Ottawa, Ontario uOttaHack 9 JAN 15 - 17 Ottawa, Ontario, CA In-Person</a>
        <a href="https://uofthacks.com/">Toronto, Ontario UofTHacks JAN 15 - 17 Toronto, Ontario, CA In-Person</a>
        """
        with patch.object(main, "get_page", return_value=BeautifulSoup(html, "html.parser")):
            events = main.find_mlh_candidates()
        self.assertEqual({event["name"] for event in events}, {"uOttaHack 9", "UofTHacks"})
        self.assertEqual(next(event for event in events if event["name"] == "uOttaHack 9")["year"], main.MLH_SEASON)

    def test_devpost_ottawa_and_campus_location(self):
        self.assertEqual(main.find_target_location("Ottawa, Canada")[0], "Ottawa")
        self.assertEqual(main.find_target_location("Sheridan College Hazel McCallion Campus")[0], "Mississauga")
        self.assertEqual(main.find_target_location("Vancouver, Canada")[0], None)

    def test_public_calendars_have_hackathon_signals(self):
        sources = {source["id"] for source in clubs.load_clubs()}
        self.assertIn("toronto_techto_calendar", sources)
        self.assertIn("markham_gdg_chapter", sources)
        self.assertIn("waterloo_engineering_ideas_clinic", sources)
        self.assertIn("ottawa_space_apps_organizer", sources)
        techto = clubs.extract_signals('<a href="/hackathon-2026">TechTO Hackathon</a><p>Tech meetup</p>', "https://luma.com/TechTO-Events")
        gdg = clubs.extract_signals('<a href="/events/hackathon">GDG Markham October Hackathon 2026</a>', "https://gdg.community.dev/gdg-markham/")
        self.assertTrue(any(clubs.signal_is_high_value({"school": "Toronto"}, s) for s in techto))
        self.assertTrue(any(clubs.signal_is_high_value({"school": "Markham"}, s) for s in gdg))
        self.assertFalse(clubs.signal_is_high_value({"school": "Waterloo"}, "TEXT|Ordinary developer conference"))
        self.assertTrue(clubs.signal_is_high_value({"school": "Ottawa", "core_event_organizer": True}, "LINK|Register|https://eventbrite.ca/e/space-apps-2027"))

    def test_advertised_annual_subdomain(self):
        source = {"school": "uOttawa", "organization": "uOttaHack", "organizer_domains": ["uottahack.ca"]}
        links = bio.extract_links('<a href="https://2027.uottahack.ca/">uOttaHack 9 | 2027</a>', "https://linktr.ee/uottahack")
        self.assertEqual(len(links), 1)
        self.assertTrue(bio.relevant(source, next(iter(links.values()))))
        unrelated = {"url": "https://other.example/", "label": "uOttaHack 9 | 2027", "context": ""}
        self.assertFalse(bio.relevant(source, unrelated))


if __name__ == "__main__":
    unittest.main()

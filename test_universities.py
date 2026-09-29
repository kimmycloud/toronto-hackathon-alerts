import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

SOURCES = {
    "TMU": "https://www.torontomu.ca/news-events/events/",
    "UofT": "https://www.utoronto.ca/events",
    "York": "https://events.yorku.ca/",
    "Waterloo": "https://uwaterloo.ca/events",
    "McMaster": "https://ses.eng.mcmaster.ca/experiences/hackathons/",
    "uOttawa": "https://www.uottawa.ca/campus-life/events-all",
    "Carleton": "https://carleton.ca/events/",
}

KEYWORDS = [
    "hackathon",
    "datathon",
    "designathon",
    "makeathon",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 HackathonAlertBot/1.0"
}


def main():
    found = {}

    for school, url in SOURCES.items():
        print(f"\nSearching {school}...")

        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=30
            )
            response.raise_for_status()
        except Exception as e:
            print(f"❌ Failed: {e}")
            continue

        soup = BeautifulSoup(response.text, "lxml")

        for link in soup.find_all("a", href=True):
            text = " ".join(link.stripped_strings).strip()

            if not text:
                continue

            if not any(
                keyword in text.lower()
                for keyword in KEYWORDS
            ):
                continue

            event_url = urljoin(url, link["href"])

            key = (text, event_url)

            found[key] = school

    print("\n========== RESULTS ==========\n")

    for (name, url), school in found.items():
        print(f"✅ [{school}] {name}")
        print(url)
        print()

    print(f"Total candidates: {len(found)}")


if __name__ == "__main__":
    main()

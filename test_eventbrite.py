import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

SEARCH_PAGES = {
    "Toronto": "https://www.eventbrite.ca/d/canada--toronto/hackathon/",
    "Hamilton": "https://www.eventbrite.ca/d/canada--hamilton/hackathon/",
    "Waterloo": "https://www.eventbrite.ca/d/canada--waterloo/hackathon/",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 HackathonAlertBot/1.0"
}

KEYWORDS = [
    "hackathon",
    "datathon",
    "designathon",
    "makeathon",
]


def main():
    found = {}

    for city, url in SEARCH_PAGES.items():
        print(f"\nSearching {city}...")

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=30
        )

        print(f"HTTP status: {response.status_code}")
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "lxml")

        for link in soup.find_all("a", href=True):
            text = " ".join(link.stripped_strings).strip()
            href = urljoin(url, link["href"])

            if "/e/" not in href:
                continue

            if not any(
                word in text.lower()
                for word in KEYWORDS
            ):
                continue

            clean_url = href.split("?")[0]

            found[clean_url] = {
                "name": text,
                "city": city,
            }

    print("\n========== RESULTS ==========\n")

    for url, event in found.items():
        print(f"✅ {event['name']}")
        print(f"Search area: {event['city']}")
        print(url)
        print()

    print(f"Total candidates: {len(found)}")


if __name__ == "__main__":
    main()

from html.parser import HTMLParser
from urllib.request import Request, urlopen
from urllib.parse import urljoin


SEARCH_PAGES = {
    "Toronto": "https://www.eventbrite.ca/d/canada--toronto/hackathon/",
    "Hamilton": "https://www.eventbrite.ca/d/canada--hamilton/hackathon/",
    "Waterloo": "https://www.eventbrite.ca/d/canada--waterloo/hackathon/",
}

KEYWORDS = [
    "hackathon",
    "datathon",
    "designathon",
    "makeathon",
]


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.current_href = None
        self.current_text = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            attrs = dict(attrs)
            self.current_href = attrs.get("href")
            self.current_text = []

    def handle_data(self, data):
        if self.current_href:
            self.current_text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.current_href:
            text = " ".join(self.current_text).strip()

            self.links.append(
                (self.current_href, text)
            )

            self.current_href = None
            self.current_text = []


def download(url):
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    with urlopen(request, timeout=30) as response:
        return response.read().decode(
            "utf-8",
            errors="ignore"
        )


def main():
    found = {}

    for city, url in SEARCH_PAGES.items():
        print(f"\nSearching {city}...")

        html = download(url)

        print(
            f"Downloaded {len(html):,} characters."
        )

        parser = LinkParser()
        parser.feed(html)

        for href, text in parser.links:

            full_url = urljoin(url, href)

            if "/e/" not in full_url:
                continue

            if not any(
                keyword in text.lower()
                for keyword in KEYWORDS
            ):
                continue

            clean_url = full_url.split("?")[0]

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

    print(
        f"Total candidates: {len(found)}"
    )


if __name__ == "__main__":
    main()

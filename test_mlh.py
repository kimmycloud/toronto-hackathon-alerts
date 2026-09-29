import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

MLH_URL = "https://www.mlh.com/seasons/2027/events"

TARGET_LOCATIONS = [
    "Toronto",
    "Scarborough",
    "Mississauga",
    "Missisauga",
    "Hamilton",
    "Waterloo",
    "Kitchener",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 HackathonAlertBot/1.0"
}


def main():
    response = requests.get(
        MLH_URL,
        headers=HEADERS,
        timeout=30
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")

    print("Matching MLH event links:\n")

    for link in soup.find_all("a", href=True):
        text = " ".join(link.stripped_strings)

        if any(
            location.lower() in text.lower()
            for location in TARGET_LOCATIONS
        ):
            print("=" * 50)
            print(text)
            print(urljoin(MLH_URL, link["href"]))


if __name__ == "__main__":
    main()

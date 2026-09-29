import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

TARGET_LOCATIONS = [
    "Toronto", "North York", "Scarborough", "Etobicoke",
    "Markham", "Mississauga", "Brampton", "Vaughan",
    "Richmond Hill", "Oakville", "Burlington", "Milton",
    "Pickering", "Ajax", "Whitby", "Oshawa",
    "Waterloo", "Kitchener", "Cambridge", "Hamilton",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 HackathonAlertBot/1.0"
}


def find_devpost_candidates():
    url = "https://devpost.com/hackathons"

    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")

    candidates = {}

    for link in soup.find_all("a", href=True):
        href = urljoin(url, link["href"])
        text = " ".join(link.stripped_strings)

        # Devpost hackathons generally have their own *.devpost.com site
        if ".devpost.com" not in href:
            continue

        if not text:
            continue

        candidates[href] = text

    return candidates


if __name__ == "__main__":
    print("Hackathon monitor started.")
    print(f"Watching {len(TARGET_LOCATIONS)} locations.")

    candidates = find_devpost_candidates()

    print(f"\nFound {len(candidates)} Devpost candidates:\n")

    for url, name in candidates.items():
        print(name)
        print(url)
        print()

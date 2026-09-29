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

        if ".devpost.com" not in href:
            continue

        if not text:
            continue

        candidates[href] = text

    return candidates


def check_location(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "lxml")
        page_text = soup.get_text(" ", strip=True).lower()

        for location in TARGET_LOCATIONS:
            if location.lower() in page_text:
                return location

    except requests.RequestException:
        pass

    return None


if __name__ == "__main__":
    print("Hackathon monitor started.")

    candidates = find_devpost_candidates()

    print(f"Found {len(candidates)} total Devpost candidates.\n")
    print("Matching our locations:\n")

    match_count = 0

    for url, name in candidates.items():
        location = check_location(url)

        if location:
            match_count += 1

            print(f"✅ {name}")
            print(f"Location match: {location}")
            print(url)
            print()

    print(f"Total location matches: {match_count}")

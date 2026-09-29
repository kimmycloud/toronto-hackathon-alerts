import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from dateparser.search import search_dates
from datetime import datetime
from zoneinfo import ZoneInfo

TARGET_LOCATIONS = [
    "Toronto", "North York", "Scarborough", "Etobicoke",
    "Markham", "Mississauga", "Brampton", "Vaughan",
    "Richmond Hill", "Oakville", "Burlington", "Milton",
    "Pickering", "Ajax", "Whitby", "Oshawa",
    "Waterloo", "Kitchener", "Cambridge", "Hamilton",
]

DEVPOST_API = "https://devpost.com/api/hackathons"

HEADERS = {
    "User-Agent": "Mozilla/5.0 HackathonAlertBot/1.0"
}

DEADLINE_KEYWORDS = [
    "application deadline",
    "applications close",
    "applications end",
    "apply by",
    "registration deadline",
    "registration closes",
    "registration ends",
    "register by",
    "sign up by",
]

LINK_KEYWORDS = [
    "apply",
    "register",
    "registration",
    "sign up",
]


def find_devpost_candidates(max_pages=20):
    all_hackathons = []
    seen = set()

    for page in range(1, max_pages + 1):
        response = requests.get(
            DEVPOST_API,
            params={"page": page},
            timeout=30
        )
        response.raise_for_status()

        hackathons = response.json().get("hackathons", [])

        if not hackathons:
            break

        for hackathon in hackathons:
            key = hackathon.get("id") or hackathon.get("url")

            if key not in seen:
                seen.add(key)
                all_hackathons.append(hackathon)

    return all_hackathons


def get_location(hackathon):
    location = hackathon.get("displayed_location")

    if isinstance(location, dict):
        return location.get("location", "")

    return location or ""


def location_matches(hackathon):
    location = get_location(hackathon).lower()

    return any(
        target.lower() in location
        for target in TARGET_LOCATIONS
    )


def get_page(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30
    )
    response.raise_for_status()

    return BeautifulSoup(response.text, "lxml")


def find_registration_links(hackathon_url):
    soup = get_page(hackathon_url)

    links = []

    for link in soup.find_all("a", href=True):
        text = " ".join(link.stripped_strings).lower()

        if any(keyword in text for keyword in LINK_KEYWORDS):
            url = urljoin(hackathon_url, link["href"])

            # Ignore obvious Devpost account/login links
            if "secure.devpost.com" in url:
                continue

            links.append(url)

    return list(dict.fromkeys(links))


def extract_deadline_from_page(url):
    try:
        soup = get_page(url)
    except Exception:
        return None

    text = soup.get_text(" ", strip=True)
    lower = text.lower()

    now = datetime.now(ZoneInfo("America/Toronto"))

    for keyword in DEADLINE_KEYWORDS:
        position = lower.find(keyword)

        if position == -1:
            continue

        # Examine text immediately around the deadline wording
        snippet = text[position:position + 180]

        # Avoid confusing project submission deadlines with signup deadlines
        if "submission deadline" in snippet.lower():
            continue

        matches = search_dates(
            snippet,
            settings={
                "PREFER_DATES_FROM": "future",
                "RELATIVE_BASE": now.replace(tzinfo=None),
            }
        )

        if not matches:
            continue

        for matched_text, date in matches:
            if date.date() >= now.date():
                return {
                    "deadline": date,
                    "matched_text": matched_text,
                    "source": url,
                    "snippet": snippet,
                }

    return None


if __name__ == "__main__":
    print("Hackathon monitor started.\n")

    hackathons = find_devpost_candidates()

    print(f"Found {len(hackathons)} Devpost hackathons.\n")

    for hackathon in hackathons:

        if not location_matches(hackathon):
            continue

        name = hackathon.get("title")
        url = hackathon.get("url")
        location = get_location(hackathon)

        print("=" * 60)
        print(f"✅ {name}")
        print(f"Location: {location}")
        print(f"Devpost dates: {hackathon.get('submission_period_dates')}")
        print(f"Page: {url}")

        # First check the hackathon page itself
        deadline = extract_deadline_from_page(url)

        # Then follow registration/application links
        registration_links = find_registration_links(url)

        print(f"Registration links found: {len(registration_links)}")

        if not deadline:
            for registration_url in registration_links:
                print(f"Checking: {registration_url}")

                deadline = extract_deadline_from_page(registration_url)

                if deadline:
                    break

        if deadline:
            print("\n🎯 POSSIBLE REGISTRATION DEADLINE")
            print(deadline["deadline"])
            print(f"Source: {deadline['source']}")
            print(f"Text: {deadline['snippet']}")

        else:
            print("\n⚠️ No verified registration deadline found.")

        print()

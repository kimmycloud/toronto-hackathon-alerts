import json
import os
import re
import requests

from bs4 import BeautifulSoup
from urllib.parse import urljoin
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
DATABASE_FILE = "sent_hackathons.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 HackathonAlertBot/1.0"
}

LINK_KEYWORDS = [
    "apply", "register", "registration", "sign up"
]

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

CLOSED_KEYWORDS = [
    "registration closed",
    "registration is closed",
    "applications closed",
    "applications are closed",
    "applications have closed",
    "registration has closed",
]


def load_sent():
    if not os.path.exists(DATABASE_FILE):
        return []

    with open(DATABASE_FILE, "r") as f:
        return json.load(f)


def save_sent(sent):
    with open(DATABASE_FILE, "w") as f:
        json.dump(sent, f, indent=2)


def get_page(url):
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, "lxml")


def find_devpost_candidates(max_pages=20):
    results = []
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
                results.append(hackathon)

    return results


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


def find_registration_links(hackathon_url):
    soup = get_page(hackathon_url)
    links = []

    # Clickable links
    for link in soup.find_all("a", href=True):
        text = " ".join(link.stripped_strings).lower()

        if any(keyword in text for keyword in LINK_KEYWORDS):
            url = urljoin(hackathon_url, link["href"])

            if "secure.devpost.com" not in url:
                links.append(url)

    # Plain-text URLs
    page_text = soup.get_text(" ", strip=True)

    raw_urls = re.findall(
        r'https?://[^\s<>"\']+',
        page_text
    )

    for url in raw_urls:
        clean_url = url.rstrip(".,);]")

        if any(domain in clean_url.lower() for domain in [
            "luma.com",
            "lu.ma",
            "eventbrite",
            "forms.gle",
            "docs.google.com/forms",
        ]):
            links.append(clean_url)

    return list(dict.fromkeys(links))


def registration_closed(url):
    try:
        soup = get_page(url)
        text = soup.get_text(" ", strip=True).lower()

        return any(
            phrase in text
            for phrase in CLOSED_KEYWORDS
        )

    except Exception:
        return False


def extract_deadline(url):
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

        snippet = text[position:position + 200]

        matches = search_dates(
            snippet,
            settings={
                "PREFER_DATES_FROM": "future",
                "RELATIVE_BASE": now.replace(tzinfo=None),
            }
        )

        if matches:
            for matched_text, date in matches:
                return {
                    "date": date,
                    "source": url,
                    "text": matched_text,
                }

    return None


def send_discord_alert(
    name,
    location,
    event_dates,
    hackathon_url,
    registration_url,
    deadline
):
    webhook = os.environ["DISCORD_WEBHOOK_URL"]

    if deadline:
        deadline_text = deadline["date"].strftime("%B %d, %Y")
        status = "🟢"
    else:
        deadline_text = "Not found — verify registration is still open"
        status = "🟡"

    message = (
        f"{status} **New Hackathon Found**\n\n"
        f"## {name}\n"
        f"📍 **Location:** {location}\n"
        f"🗓️ **Hackathon:** {event_dates}\n"
        f"⏰ **Registration deadline:** {deadline_text}\n\n"
        f"🔗 **Hackathon:** {hackathon_url}\n"
    )

    if registration_url:
        message += f"📝 **Register:** {registration_url}\n"

    requests.post(
        webhook,
        json={"content": message},
        timeout=30
    ).raise_for_status()


def main():
    print("Hackathon monitor started.")

    sent = load_sent()
    hackathons = find_devpost_candidates()

    print(f"Found {len(hackathons)} Devpost hackathons.")

    for hackathon in hackathons:

        if not location_matches(hackathon):
            continue

        # Devpost says this event isn't upcoming
        if hackathon.get("open_state") != "upcoming":
            continue

        name = hackathon.get("title", "Unknown")
        url = hackathon.get("url")
        location = get_location(hackathon)
        event_dates = hackathon.get(
            "submission_period_dates",
            "Date unavailable"
        )

        # Already announced
        if url in sent:
            continue

        print(f"\nChecking: {name}")

        registration_links = find_registration_links(url)
        registration_url = (
            registration_links[0]
            if registration_links
            else None
        )

        # If registration page clearly says closed → skip
        if registration_url and registration_closed(registration_url):
            print("❌ Registration closed.")
            continue

        deadline = extract_deadline(url)

        if not deadline and registration_url:
            deadline = extract_deadline(registration_url)

        # If we actually found a deadline and it is already past → skip
        if deadline:
            now = datetime.now(ZoneInfo("America/Toronto"))

            deadline_date = deadline["date"]

            if deadline_date.date() < now.date():
                print("❌ Deadline passed.")
                continue

        send_discord_alert(
            name,
            location,
            event_dates,
            url,
            registration_url,
            deadline,
        )

        sent.append(url)
        save_sent(sent)

        print("✅ Discord alert sent.")


if __name__ == "__main__":
    main()

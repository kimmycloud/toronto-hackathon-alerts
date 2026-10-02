import json
import os
import re
import requests

from bs4 import BeautifulSoup
from urllib.parse import urljoin
from dateparser.search import search_dates
from datetime import datetime
from zoneinfo import ZoneInfo


# ---------- SETTINGS ----------

DEVPOST_API = "https://devpost.com/api/hackathons"

now_toronto = datetime.now(ZoneInfo("America/Toronto"))
MLH_SEASON = now_toronto.year + (now_toronto.month >= 9)
MLH_URL = f"https://www.mlh.com/seasons/{MLH_SEASON}/events"

DATABASE_FILE = "sent_hackathons.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 HackathonAlertBot/1.0"
}

LOCATION_ALIASES = {
    "toronto": "Toronto",
    "north york": "North York",
    "scarborough": "Scarborough",
    "etobicoke": "Etobicoke",
    "markham": "Markham",
    "mississauga": "Mississauga",
    "missisauga": "Mississauga",  # MLH typo
    "brampton": "Brampton",
    "vaughan": "Vaughan",
    "richmond hill": "Richmond Hill",
    "oakville": "Oakville",
    "burlington": "Burlington",
    "milton": "Milton",
    "pickering": "Pickering",
    "ajax": "Ajax",
    "whitby": "Whitby",
    "oshawa": "Oshawa",
    "waterloo": "Waterloo",
    "kitchener": "Kitchener",
    "cambridge": "Cambridge",
    "hamilton": "Hamilton",
    "ottawa": "Ottawa",
    # Devpost sometimes publishes a campus name instead of its city.
    "sheridan college hazel mccallion campus": "Mississauga",
}

LINK_KEYWORDS = [
    "apply",
    "register",
    "registration",
    "sign up",
    "rsvp",
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
    "rsvp by",
]

CLOSED_KEYWORDS = [
    "registration closed",
    "registration is closed",
    "registration has closed",
    "applications closed",
    "applications are closed",
    "applications have closed",
    "registration ended",
    "applications ended",
]

MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4,
    "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8,
    "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

MONTH_PATTERN = "|".join(MONTHS.keys())

MLH_DATE_RE = re.compile(
    rf"\b({MONTH_PATTERN})\s+(\d{{1,2}})\s*[–—-]\s*"
    rf"(?:(%s)\s+)?(\d{{1,2}})\b" % MONTH_PATTERN,
    re.IGNORECASE,
)


# ---------- DATABASE ----------

def load_sent():
    if not os.path.exists(DATABASE_FILE):
        return set()

    with open(DATABASE_FILE, "r") as f:
        return set(json.load(f))


def save_sent(sent):
    with open(DATABASE_FILE, "w") as f:
        json.dump(sorted(sent), f, indent=2)


def normalize_name(name):
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def event_key(name, year):
    return f"{normalize_name(name)}::{year}"


# ---------- GENERAL WEB HELPERS ----------

def get_page(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()

    return BeautifulSoup(response.text, "lxml")


def find_target_location(text):
    lower = text.lower()

    for alias, canonical in LOCATION_ALIASES.items():
        if alias in lower:
            return canonical, alias

    return None, None


def find_registration_links(event_url):
    try:
        soup = get_page(event_url)
    except Exception:
        return []

    links = []

    # Clickable links
    for link in soup.find_all("a", href=True):
        text = " ".join(link.stripped_strings).lower()

        if any(keyword in text for keyword in LINK_KEYWORDS):
            url = urljoin(event_url, link["href"])

            if "secure.devpost.com" not in url:
                links.append(url)

    # Plain-text registration URLs
    page_text = soup.get_text(" ", strip=True)

    raw_urls = re.findall(
        r'https?://[^\s<>"\']+',
        page_text
    )

    for url in raw_urls:
        clean = url.rstrip(".,);]")

        if any(domain in clean.lower() for domain in [
            "luma.com",
            "lu.ma",
            "eventbrite",
            "forms.gle",
            "docs.google.com/forms",
        ]):
            links.append(clean)

    return list(dict.fromkeys(links))


def registration_closed(url):
    try:
        text = get_page(url).get_text(
            " ",
            strip=True
        ).lower()

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

    now = datetime.now(
        ZoneInfo("America/Toronto")
    )

    for keyword in DEADLINE_KEYWORDS:
        position = lower.find(keyword)

        if position == -1:
            continue

        snippet = text[position:position + 220]

        matches = search_dates(
            snippet,
            settings={
                "PREFER_DATES_FROM": "future",
                "RELATIVE_BASE": now.replace(
                    tzinfo=None
                ),
            },
        )

        if matches:
            for matched_text, date in matches:
                return {
                    "date": date,
                    "source": url,
                    "text": matched_text,
                }

    return None


# ---------- DEVPOST ----------

def find_devpost_candidates(max_pages=20):
    results = []
    seen = set()

    for page in range(1, max_pages + 1):
        response = requests.get(
            DEVPOST_API,
            params={"page": page},
            timeout=30,
        )
        response.raise_for_status()

        hackathons = response.json().get(
            "hackathons",
            []
        )

        if not hackathons:
            break

        for hackathon in hackathons:
            key = (
                hackathon.get("id")
                or hackathon.get("url")
            )

            if key not in seen:
                seen.add(key)
                results.append(hackathon)

    return results


def get_devpost_location(hackathon):
    location = hackathon.get(
        "displayed_location"
    )

    if isinstance(location, dict):
        return location.get("location", "")

    return location or ""


def get_year_from_text(text):
    match = re.search(r"\b(20\d{2})\b", text or "")

    if match:
        return int(match.group(1))

    return datetime.now().year


# ---------- MLH ----------

def mlh_year_for_month(month):
    # MLH "2027 season" contains Fall 2026 + Winter/Spring 2027
    if month >= 9:
        return MLH_SEASON - 1

    return MLH_SEASON


def find_mlh_candidates():
    soup = get_page(MLH_URL)

    events = []
    seen = set()

    for link in soup.find_all("a", href=True):
        text = " ".join(link.stripped_strings)

        location, alias = find_target_location(text)

        if not location:
            continue

        date_match = MLH_DATE_RE.search(text)

        if not date_match:
            continue

        start_month = MONTHS[
            date_match.group(1).upper()
        ]

        start_day = int(
            date_match.group(2)
        )

        end_month = (
            MONTHS[date_match.group(3).upper()]
            if date_match.group(3)
            else start_month
        )

        end_day = int(
            date_match.group(4)
        )

        start_year = mlh_year_for_month(
            start_month
        )

        end_year = mlh_year_for_month(
            end_month
        )

        start_date = datetime(
            start_year,
            start_month,
            start_day,
        )

        end_date = datetime(
            end_year,
            end_month,
            end_day,
        )

        # Event name is the text before the date
        prefix = text[:date_match.start()].strip()

        prefix = re.sub(
            rf"^\s*{re.escape(alias)}\s*,?\s*Ontario\s*",
            "",
            prefix,
            flags=re.IGNORECASE,
        ).strip()

        name = prefix

        if not name:
            continue

        url = urljoin(
            MLH_URL,
            link["href"]
        )

        # Remove MLH tracking parameters
        url = url.split("?")[0]

        key = event_key(
            name,
            start_year
        )

        if key in seen:
            continue

        seen.add(key)

        if start_month == end_month:
            event_dates = (
                f"{start_date.strftime('%b')} "
                f"{start_day}–{end_day}, "
                f"{start_year}"
            )
        else:
            event_dates = (
                f"{start_date.strftime('%b')} "
                f"{start_day}–"
                f"{end_date.strftime('%b')} "
                f"{end_day}, {end_year}"
            )

        events.append({
            "name": name,
            "location": location,
            "url": url,
            "start_date": start_date,
            "end_date": end_date,
            "event_dates": event_dates,
            "year": start_year,
            "source": "MLH",
        })

    return events


# ---------- DISCORD ----------

def send_discord_alert(
    name,
    location,
    event_dates,
    event_url,
    registration_url,
    deadline,
    source,
):
    webhook = os.environ[
        "DISCORD_WEBHOOK_URL"
    ]

    if deadline:
        deadline_text = deadline[
            "date"
        ].strftime("%B %d, %Y")

        status = "🟢"

    else:
        deadline_text = (
            "Not found — verify registration "
            "is still open"
        )

        status = "🟡"

    message = (
        f"{status} **New Hackathon Found**\n\n"
        f"## {name}\n"
        f"📍 **Location:** {location}\n"
        f"🗓️ **Hackathon:** {event_dates}\n"
        f"⏰ **Registration deadline:** "
        f"{deadline_text}\n"
        f"🔎 **Found via:** {source}\n\n"
        f"🔗 **Hackathon:** {event_url}\n"
    )

    if registration_url:
        message += (
            f"📝 **Register:** "
            f"{registration_url}\n"
        )

    response = requests.post(
        webhook,
        json={"content": message},
        timeout=30,
    )

    response.raise_for_status()


# ---------- PROCESS EVENT ----------

def process_event(
    name,
    location,
    event_dates,
    event_url,
    year,
    source,
    sent,
):
    key = event_key(name, year)

    # Prevent duplicates across MLH + Devpost
    if key in sent or event_url in sent:
        return

    print(f"\nChecking: {name} [{source}]")

    # Official page explicitly says registration closed
    if registration_closed(event_url):
        print("❌ Registration closed.")
        return

    registration_links = find_registration_links(
        event_url
    )

    registration_url = (
        registration_links[0]
        if registration_links
        else None
    )

    if (
        registration_url
        and registration_closed(registration_url)
    ):
        print("❌ Registration closed.")
        return

    deadline = extract_deadline(
        event_url
    )

    if not deadline and registration_url:
        deadline = extract_deadline(
            registration_url
        )

    if deadline:
        now = datetime.now(
            ZoneInfo("America/Toronto")
        )

        if deadline["date"].date() < now.date():
            print("❌ Registration deadline passed.")
            return

    send_discord_alert(
        name,
        location,
        event_dates,
        event_url,
        registration_url,
        deadline,
        source,
    )

    # Store BOTH so cross-site duplicates are suppressed
    sent.add(key)
    sent.add(event_url)

    save_sent(sent)

    print("✅ Discord alert sent.")


# ---------- MAIN ----------

def main():
    print("Hackathon monitor started.")

    sent = load_sent()

    now = datetime.now(
        ZoneInfo("America/Toronto")
    )

    # ----- DEVPOST -----

    devpost_events = find_devpost_candidates()

    print(
        f"Found {len(devpost_events)} "
        f"Devpost hackathons."
    )

    for hackathon in devpost_events:

        location_raw = get_devpost_location(
            hackathon
        )

        location, _ = find_target_location(
            location_raw
        )

        if not location:
            continue

        if hackathon.get("open_state") != "upcoming":
            continue

        name = hackathon.get(
            "title",
            "Unknown"
        )

        url = hackathon.get("url")

        event_dates = hackathon.get(
            "submission_period_dates",
            "Date unavailable",
        )

        year = get_year_from_text(
            event_dates
        )

        process_event(
            name=name,
            location=location,
            event_dates=event_dates,
            event_url=url,
            year=year,
            source="Devpost",
            sent=sent,
        )

    # ----- MLH -----

    mlh_events = find_mlh_candidates()

    print(
        f"Found {len(mlh_events)} "
        f"matching MLH hackathons."
    )

    for event in mlh_events:

        # Ignore events that have already started/passed
        if event["start_date"].date() <= now.date():
            continue

        process_event(
            name=event["name"],
            location=event["location"],
            event_dates=event["event_dates"],
            event_url=event["url"],
            year=event["year"],
            source="MLH",
            sent=sent,
        )


if __name__ == "__main__":
    main()

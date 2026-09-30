import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser


SOURCES_FILE = Path("university_sources.json")

HEADERS = {
    "User-Agent": "hackathon-alert-monitor/1.0"
}


def load_sources():
    with SOURCES_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)

    return [
        source
        for source in data["sources"]
        if source.get("active", True)
    ]


def fetch_html(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    return response.text


def parse_luma_schema_org(source):
    html = fetch_html(source["url"])
    soup = BeautifulSoup(html, "html.parser")

    events = []

    for script in soup.find_all(
        "script",
        attrs={"type": "application/ld+json"},
    ):
        raw = script.string

        if not raw:
            continue

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        items = []

        if isinstance(data, dict):
            if data.get("@type") == "ItemList":
                items.extend(
                    data.get("itemListElement", [])
                )

            elif data.get("@type") == "Event":
                items.append(data)

        elif isinstance(data, list):
            items.extend(data)

        for item in items:
            if isinstance(item, dict) and "item" in item:
                item = item["item"]

            if not isinstance(item, dict):
                continue

            if item.get("@type") != "Event":
                continue

            start_raw = item.get("startDate")

            if not start_raw:
                continue

            try:
                start = date_parser.parse(start_raw)
            except Exception:
                continue

            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)

            location = item.get("location")

            if isinstance(location, dict):
                location_name = (
                    location.get("name")
                    or location.get("address")
                    or ""
                )
            else:
                location_name = str(location or "")

            events.append(
                {
                    "source_id": source["id"],
                    "school": source["school"],
                    "organizer": source["name"],
                    "title": item.get("name", ""),
                    "start": start.isoformat(),
                    "end": item.get("endDate"),
                    "location": location_name,
                    "url": item.get("url", source["url"]),
                }
            )

    return events


def parse_next_data_future_events(source):
    html = fetch_html(source["url"])
    soup = BeautifulSoup(html, "html.parser")

    script = soup.find(
        "script",
        id="__NEXT_DATA__",
    )

    if not script or not script.string:
        raise RuntimeError(
            "__NEXT_DATA__ not found"
        )

    data = json.loads(script.string)

    future_events = (
        data
        .get("props", {})
        .get("pageProps", {})
        .get("futureEvents", [])
    )

    events = []

    for item in future_events:
        if not isinstance(item, dict):
            continue

        start_raw = (
            item.get("startDate")
            or item.get("start")
            or item.get("date")
        )

        if not start_raw:
            continue

        try:
            start = date_parser.parse(
                str(start_raw)
            )
        except Exception:
            continue

        if start.tzinfo is None:
            start = start.replace(
                tzinfo=timezone.utc
            )

        title = (
            item.get("name")
            or item.get("title")
            or ""
        )

        location = (
            item.get("location")
            or item.get("venue")
            or ""
        )

        url = (
            item.get("url")
            or item.get("link")
            or source["url"]
        )

        events.append(
            {
                "source_id": source["id"],
                "school": source["school"],
                "organizer": source["name"],
                "title": str(title),
                "start": start.isoformat(),
                "end": (
                    item.get("endDate")
                    or item.get("end")
                ),
                "location": str(location),
                "url": str(url),
            }
        )

    return events


def parse_html_upcoming_events(source):
    html = fetch_html(source["url"])
    soup = BeautifulSoup(html, "html.parser")

    events = []

    date_pattern = re.compile(
        r"\b("
        r"Jan(?:uary)?|"
        r"Feb(?:ruary)?|"
        r"Mar(?:ch)?|"
        r"Apr(?:il)?|"
        r"May|"
        r"Jun(?:e)?|"
        r"Jul(?:y)?|"
        r"Aug(?:ust)?|"
        r"Sep(?:tember)?|"
        r"Oct(?:ober)?|"
        r"Nov(?:ember)?|"
        r"Dec(?:ember)?"
        r")\s+\d{1,2}"
        r"(?:,\s+\d{4})?",
        re.IGNORECASE,
    )

    for element in soup.find_all(
        ["article", "section", "div", "li"]
    ):
        block_text = element.get_text(
            " ",
            strip=True,
        )

        if not block_text:
            continue

        match = date_pattern.search(block_text)

        if not match:
            continue

        try:
            start = date_parser.parse(
                match.group(0),
                fuzzy=True,
            )
        except Exception:
            continue

        if start.tzinfo is None:
            start = start.replace(
                tzinfo=timezone.utc
            )

        heading = element.find(
            ["h1", "h2", "h3", "h4", "strong"]
        )

        if heading:
            title = heading.get_text(
                " ",
                strip=True,
            )
        else:
            title = block_text[:120]

        link = element.find(
            "a",
            href=True,
        )

        if link:
            url = urljoin(
                source["url"],
                link["href"],
            )
        else:
            url = source["url"]

        events.append(
            {
                "source_id": source["id"],
                "school": source["school"],
                "organizer": source["name"],
                "title": title,
                "start": start.isoformat(),
                "end": None,
                "location": "",
                "url": url,
            }
        )

    deduped = []
    seen = set()

    for event in events:
        key = (
            event["title"].lower(),
            event["start"],
        )

        if key in seen:
            continue

        seen.add(key)
        deduped.append(event)

    return deduped


PARSERS = {
    "luma_schema_org": parse_luma_schema_org,
    "next_data_future_events": parse_next_data_future_events,
    "html_upcoming_events": parse_html_upcoming_events,
}


def is_future_event(event):
    try:
        start = date_parser.parse(
            event["start"]
        )
    except Exception:
        return False

    if start.tzinfo is None:
        start = start.replace(
            tzinfo=timezone.utc
        )

    return (
        start
        > datetime.now(timezone.utc)
    )


def main():
    sources = load_sources()

    print(
        f"Loaded {len(sources)} "
        f"active university sources."
    )

    tested = 0
    found = 0

    for source in sources:
        parser_name = source.get("parser")

        if parser_name not in PARSERS:
            continue

        parser = PARSERS[parser_name]

        print(
            f"\nChecking: "
            f"{source['school']} — "
            f"{source['name']}"
        )

        try:
            events = parser(source)

            future = [
                event
                for event in events
                if is_future_event(event)
            ]

            tested += 1
            found += len(future)

            print(
                f"SUCCEEDED: "
                f"{len(events)} events parsed, "
                f"{len(future)} future."
            )

            for event in future:
                print(
                    f"  FUTURE: "
                    f"{event['title']} "
                    f"| {event['start']}"
                )

        except Exception as exc:
            print(
                f"FAILED: "
                f"{source['id']}: "
                f"{exc}"
            )

    print()

    print(
        f"Finished: "
        f"{tested} sources tested, "
        f"{found} future events found."
    )


if __name__ == "__main__":
    main()

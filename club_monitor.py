import json
from datetime import datetime, timezone
from pathlib import Path

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


PARSERS = {
    "luma_schema_org": parse_luma_schema_org,
}


def is_future_event(event):
    try:
        start = date_parser.parse(event["start"])
    except Exception:
        return False

    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)

    return start > datetime.now(timezone.utc)


def main():
    sources = load_sources()

    print(
        f"Loaded {len(sources)} active university sources."
    )

    tested = 0
    found = 0

    for source in sources:
        parser_name = source.get("parser")

        # We are implementing parsers gradually.
        if parser_name not in PARSERS:
            continue

        parser = PARSERS[parser_name]

        print(
            f"\nChecking: "
            f"{source['school']} — {source['name']}"
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
                    f"  FUTURE: {event['title']} "
                    f"| {event['start']}"
                )

        except Exception as exc:
            print(
                f"FAILED: {source['id']}: {exc}"
            )

    print()
    print(
        f"Finished: {tested} sources tested, "
        f"{found} future events found."
    )


if __name__ == "__main__":
    main()

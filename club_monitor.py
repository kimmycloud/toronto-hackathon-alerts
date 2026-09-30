import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser


SOURCES_FILE = Path("university_sources.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "en-CA,en;q=0.9",
}


class SourceBlockedError(Exception):
    pass


def load_sources():
    with SOURCES_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)

    return [
        source
        for source in data["sources"]
        if source.get("active", True)
    ]


def fetch_url(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30,
        allow_redirects=True,
    )

    if response.status_code == 403:
        raise SourceBlockedError(
            f"{url} -> HTTP 403"
        )

    response.raise_for_status()

    return response.text, response.url


def fetch_html(source):
    urls = [source["url"]]

    for backup in source.get("backup_urls", []):
        if backup and backup not in urls:
            urls.append(backup)

    errors = []

    for url in urls:
        try:
            return fetch_url(url)

        except SourceBlockedError as exc:
            errors.append(str(exc))

        except requests.RequestException as exc:
            errors.append(
                f"{url} -> {exc}"
            )

    if errors and all(
        "HTTP 403" in error
        for error in errors
    ):
        raise SourceBlockedError(
            "; ".join(errors)
        )

    raise RuntimeError(
        "; ".join(errors)
    )


def normalize_datetime(value):
    if value is None:
        return None

    try:
        # Unix timestamp support
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(
                value,
                tz=timezone.utc,
            )

        value_string = str(value).strip()

        if value_string.isdigit():
            number = int(value_string)

            if number > 1000000000:
                return datetime.fromtimestamp(
                    number,
                    tz=timezone.utc,
                )

        dt = date_parser.parse(
            value_string
        )

    except Exception:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(
            tzinfo=timezone.utc
        )

    return dt


def make_event(
    source,
    title,
    start,
    url,
    location="",
    end=None,
):
    return {
        "source_id": source["id"],
        "school": source["school"],
        "organizer": source["name"],
        "title": str(title or "").strip(),
        "start": start.isoformat(),
        "end": end,
        "location": str(location or ""),
        "url": str(url),
    }


def dedupe_events(events):
    deduped = []
    seen = set()

    for event in events:
        key = (
            event.get("title", "").strip().lower(),
            event.get("start"),
            event.get("url"),
        )

        if key in seen:
            continue

        seen.add(key)
        deduped.append(event)

    return deduped


def parse_luma_schema_org(source):
    html, final_url = fetch_html(source)
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
                    data.get(
                        "itemListElement",
                        [],
                    )
                )

            elif data.get("@type") == "Event":
                items.append(data)

        elif isinstance(data, list):
            items.extend(data)

        for item in items:
            if (
                isinstance(item, dict)
                and "item" in item
            ):
                item = item["item"]

            if not isinstance(item, dict):
                continue

            if item.get("@type") != "Event":
                continue

            start = normalize_datetime(
                item.get("startDate")
            )

            if not start:
                continue

            location = item.get(
                "location"
            )

            if isinstance(location, dict):
                location_name = (
                    location.get("name")
                    or location.get("address")
                    or ""
                )
            else:
                location_name = str(
                    location or ""
                )

            events.append(
                make_event(
                    source=source,
                    title=item.get(
                        "name",
                        "",
                    ),
                    start=start,
                    end=item.get(
                        "endDate"
                    ),
                    location=location_name,
                    url=item.get(
                        "url",
                        final_url,
                    ),
                )
            )

    return dedupe_events(events)


def parse_next_data_future_events(source):
    html, final_url = fetch_html(source)
    soup = BeautifulSoup(html, "html.parser")

    script = soup.find(
        "script",
        id="__NEXT_DATA__",
    )

    if not script or not script.string:
        raise RuntimeError(
            "__NEXT_DATA__ not found"
        )

    data = json.loads(
        script.string
    )

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

        start = normalize_datetime(
            start_raw
        )

        if not start:
            continue

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
            or final_url
        )

        events.append(
            make_event(
                source=source,
                title=title,
                start=start,
                end=(
                    item.get("endDate")
                    or item.get("end")
                ),
                location=location,
                url=url,
            )
        )

    return dedupe_events(events)


def parse_html_upcoming_events(source):
    html, final_url = fetch_html(source)
    soup = BeautifulSoup(html, "html.parser")

    events = []

    month_names = (
        "January February March April "
        "May June July August September "
        "October November December "
        "Jan Feb Mar Apr Jun Jul Aug "
        "Sep Sept Oct Nov Dec"
    ).split()

    for element in soup.find_all(
        ["article", "section", "div", "li"]
    ):
        block_text = element.get_text(
            " ",
            strip=True,
        )

        if not block_text:
            continue

        lower = block_text.lower()

        if not any(
            month.lower() in lower
            for month in month_names
        ):
            continue

        try:
            start = date_parser.parse(
                block_text,
                fuzzy=True,
                default=datetime.now(
                    timezone.utc
                ).replace(
                    hour=0,
                    minute=0,
                    second=0,
                    microsecond=0,
                ),
            )

        except Exception:
            continue

        if start.tzinfo is None:
            start = start.replace(
                tzinfo=timezone.utc
            )

        heading = element.find(
            [
                "h1",
                "h2",
                "h3",
                "h4",
                "strong",
            ]
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
            event_url = urljoin(
                final_url,
                link["href"],
            )
        else:
            event_url = final_url

        events.append(
            make_event(
                source=source,
                title=title,
                start=start,
                url=event_url,
            )
        )

    return dedupe_events(events)


def parse_gdg_event_cards(source):
    html, final_url = fetch_html(source)
    soup = BeautifulSoup(html, "html.parser")

    events = []

    # Prefer JSON-LD.
    for script in soup.find_all(
        "script",
        attrs={
            "type": "application/ld+json"
        },
    ):
        raw = script.string

        if not raw:
            continue

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if isinstance(data, dict):
            candidates = [data]

            graph = data.get("@graph")

            if isinstance(graph, list):
                candidates.extend(graph)

        elif isinstance(data, list):
            candidates = data

        else:
            candidates = []

        for item in candidates:
            if not isinstance(
                item,
                dict,
            ):
                continue

            if item.get(
                "@type"
            ) != "Event":
                continue

            start = normalize_datetime(
                item.get("startDate")
            )

            if not start:
                continue

            location = item.get(
                "location"
            )

            location_name = ""

            if isinstance(
                location,
                dict,
            ):
                location_name = (
                    location.get("name")
                    or ""
                )

                address = location.get(
                    "address"
                )

                if isinstance(
                    address,
                    dict,
                ):
                    address_text = ", ".join(
                        str(
                            address.get(key)
                        )
                        for key in [
                            "streetAddress",
                            "addressLocality",
                            "addressRegion",
                        ]
                        if address.get(key)
                    )

                    if address_text:
                        if location_name:
                            location_name += (
                                f" — {address_text}"
                            )
                        else:
                            location_name = (
                                address_text
                            )

            events.append(
                make_event(
                    source=source,
                    title=item.get(
                        "name",
                        "",
                    ),
                    start=start,
                    end=item.get(
                        "endDate"
                    ),
                    location=location_name,
                    url=item.get(
                        "url",
                        final_url,
                    ),
                )
            )

    # Fallback to visible event links.
    if not events:
        month_words = (
            "jan feb mar apr may jun jul "
            "aug sep sept oct nov dec "
            "january february march april "
            "june july august september "
            "october november december"
        ).split()

        for link in soup.find_all(
            "a",
            href=True,
        ):
            text = link.get_text(
                " ",
                strip=True,
            )

            if not text:
                continue

            lower = text.lower()

            if (
                "no upcoming events"
                in lower
            ):
                continue

            if not any(
                month in lower
                for month in month_words
            ):
                continue

            try:
                start = date_parser.parse(
                    text,
                    fuzzy=True,
                )

            except Exception:
                continue

            if start.tzinfo is None:
                start = start.replace(
                    tzinfo=timezone.utc
                )

            events.append(
                make_event(
                    source=source,
                    title=text[:160],
                    start=start,
                    url=urljoin(
                        final_url,
                        link["href"],
                    ),
                )
            )

    return dedupe_events(events)


def is_itch_jam_url(url):
    try:
        parsed = urlparse(url)

        host = parsed.netloc.lower()

        return (
            host.endswith("itch.io")
            and "/jam/" in parsed.path
        )

    except Exception:
        return False


def extract_itch_datetime(element):
    attributes = [
        "datetime",
        "title",
        "data-time",
        "data-timestamp",
        "data-date",
    ]

    for attribute in attributes:
        value = element.get(
            attribute
        )

        if not value:
            continue

        dt = normalize_datetime(
            value
        )

        if dt:
            return dt

    text = element.get_text(
        " ",
        strip=True,
    )

    if text:
        return normalize_datetime(
            text
        )

    return None


def parse_itch_jam_page(
    source,
    jam_url,
):
    html, final_url = fetch_url(
        jam_url
    )

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    title_element = soup.find("h1")

    if title_element:
        title = title_element.get_text(
            " ",
            strip=True,
        )
    elif soup.title:
        title = soup.title.get_text(
            " ",
            strip=True,
        )
    else:
        title = "itch.io jam"

    date_candidates = []

    # itch.io commonly exposes formatted dates
    # in abbr/time elements.
    for element in soup.find_all(
        ["abbr", "time"]
    ):
        dt = extract_itch_datetime(
            element
        )

        if dt:
            date_candidates.append(dt)

    # Also inspect elements with timestamp attrs.
    for element in soup.find_all(
        attrs={"data-time": True}
    ):
        dt = extract_itch_datetime(
            element
        )

        if dt:
            date_candidates.append(dt)

    for element in soup.find_all(
        attrs={"data-timestamp": True}
    ):
        dt = extract_itch_datetime(
            element
        )

        if dt:
            date_candidates.append(dt)

    if not date_candidates:
        return None

    # The earliest exposed jam date is normally
    # the start/opening date.
    start = min(
        date_candidates
    )

    end = None

    if len(date_candidates) > 1:
        latest = max(
            date_candidates
        )

        if latest != start:
            end = latest.isoformat()

    return make_event(
        source=source,
        title=title,
        start=start,
        end=end,
        location="Online / see jam page",
        url=final_url,
    )


def parse_itch_io_jams(source):
    candidate_urls = []

    primary_urls = [
        source["url"],
        *source.get(
            "backup_urls",
            [],
        ),
    ]

    # Direct jam links from registry.
    for url in primary_urls:
        if is_itch_jam_url(url):
            candidate_urls.append(url)

    # Search organizer/profile pages for
    # links to hosted jams.
    for profile_url in primary_urls:
        if is_itch_jam_url(
            profile_url
        ):
            continue

        try:
            html, final_url = fetch_url(
                profile_url
            )

        except SourceBlockedError:
            continue

        except requests.RequestException:
            continue

        soup = BeautifulSoup(
            html,
            "html.parser",
        )

        for link in soup.find_all(
            "a",
            href=True,
        ):
            linked_url = urljoin(
                final_url,
                link["href"],
            )

            if is_itch_jam_url(
                linked_url
            ):
                candidate_urls.append(
                    linked_url
                )

    candidate_urls = list(
        dict.fromkeys(
            candidate_urls
        )
    )

    events = []

    for jam_url in candidate_urls:
        try:
            event = parse_itch_jam_page(
                source,
                jam_url,
            )

        except SourceBlockedError:
            continue

        except requests.RequestException:
            continue

        if event:
            events.append(event)

    return dedupe_events(events)


PARSERS = {
    "luma_schema_org":
        parse_luma_schema_org,

    "next_data_future_events":
        parse_next_data_future_events,

    "html_upcoming_events":
        parse_html_upcoming_events,

    "gdg_event_cards":
        parse_gdg_event_cards,

    "itch_io_jams":
        parse_itch_io_jams,
}


def is_future_event(event):
    start = normalize_datetime(
        event["start"]
    )

    if not start:
        return False

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

    succeeded = 0
    blocked = 0
    failed = 0
    found = 0

    for source in sources:
        parser_name = source.get(
            "parser"
        )

        if parser_name not in PARSERS:
            continue

        parser = PARSERS[
            parser_name
        ]

        print(
            f"\nChecking: "
            f"{source['school']} — "
            f"{source['name']}"
        )

        try:
            events = parser(
                source
            )

            future = [
                event
                for event in events
                if is_future_event(
                    event
                )
            ]

            succeeded += 1
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

        except SourceBlockedError as exc:
            blocked += 1

            print(
                f"BLOCKED: "
                f"{source['id']}: "
                f"{exc}"
            )

        except Exception as exc:
            failed += 1

            print(
                f"FAILED: "
                f"{source['id']}: "
                f"{exc}"
            )

    print()

    print(
        f"Finished: "
        f"{succeeded} succeeded, "
        f"{blocked} blocked, "
        f"{failed} failed, "
        f"{found} future events found."
    )


if __name__ == "__main__":
    main()

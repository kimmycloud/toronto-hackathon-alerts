#!/usr/bin/env python3
"""
Public university event discovery.

Python 3.10+
Requires: lxml

Examples:

Diagnostic JSON:
    python discover.py

Production notification run:
    python discover.py \
        --sources tmu waterloo uottawa carleton carleton_scs \
        --notify

Include all campus events for debugging:
    python discover.py --all-events
"""

import argparse
import datetime as dt
import hashlib
import html as html_std
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

from lxml import html


TZ = ZoneInfo("America/Toronto")

UA = "UniversityHackathonDiscovery/2.0"

STATE_FILE = Path(
    "university_discovery_state.json"
)

DISCORD_WEBHOOK_URL = os.environ.get(
    "DISCORD_WEBHOOK_URL"
)


# --------------------------------------------------
# Relevant event keywords
# --------------------------------------------------

HACK = re.compile(
    r"\bhackathons?\b"
    r"|\b(?:datathon|makeathon|designathon|buildathon|codeathon)s?\b"
    r"|\bgame\s*jams?\b"
    r"|\bcapture\s+the\s+flag\b"
    r"|\bctf\b"
    r"|\bcoding\s+(?:competition|contest)s?\b"
    r"|\bprogramming\s+(?:competition|contest)s?\b"
    r"|\bcompetitive\s+programming\b"
    r"|\brobotics\s+(?:competition|challenge)s?\b"
    r"|\bengineering\s+(?:competition|challenge)s?\b"
    r"|\binnovation\s+challenge\b"
    r"|\b(?:ai|ml|data)\s+(?:competition|challenge)s?\b"
    r"|\bcase\s+competition\b"
    r"|\buoft?hacks\b"
    r"|\bellehacks\b"
    r"|\bdeltahacks\b"
    r"|\buottahack\b"
    r"|\bcuhacking\b"
    r"|\bhack(?:rx| the north| the valley| the hill| the world)\b"
    r"|\bspeed\s*hack\b",
    re.I,
)


MONTHS = {
    name.lower(): i
    for i, name in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        1,
    )
}

MONTHS.update(
    {
        key[:3]: value
        for key, value in list(
            MONTHS.items()
        )
    }
)

MONTH = (
    r"(?:January|February|March|April|May|June|"
    r"July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
)

DATE = re.compile(
    r"\b("
    + MONTH
    + r")\s+(\d{1,2}),?\s+(\d{4})\b",
    re.I,
)

RANGE = re.compile(
    r"\b("
    + MONTH
    + r")\s+(\d{1,2})\s*[-–—]\s*"
    r"(\d{1,2}),?\s+(\d{4})\b",
    re.I,
)


# --------------------------------------------------
# Helpers
# --------------------------------------------------

def clean(value):
    return " ".join(
        html_std.unescape(
            value or ""
        ).split()
    )


def text(node):
    return clean(
        " ".join(
            node.xpath(
                ".//text()["
                "not(ancestor::script) "
                "and not(ancestor::style)"
                "]"
            )
        )
    )


def cls(name):
    return (
        'contains(concat(" ",'
        'normalize-space(@class)," "),'
        '" '
        + name
        + ' ")'
    )


def dates(value):
    """
    Parse ONLY explicit event date labels.

    Never infer a year from today's date.
    """

    match = RANGE.search(value)

    if match:
        month, first_day, last_day, year = (
            match.groups()
        )

        start = dt.date(
            int(year),
            MONTHS[
                month.lower()
            ],
            int(first_day),
        )

        end = dt.date(
            int(year),
            MONTHS[
                month.lower()
            ],
            int(last_day),
        )

        return (
            start.isoformat(),
            end.isoformat(),
        )

    found = [
        dt.date(
            int(year),
            MONTHS[
                month.lower()
            ],
            int(day),
        ).isoformat()
        for month, day, year
        in DATE.findall(value)
    ]

    if not found:
        return None, None

    return (
        found[0],
        found[-1],
    )


# --------------------------------------------------
# HTTP
# --------------------------------------------------

class SourceBlockedError(
    RuntimeError
):
    pass


class SourceUnavailableError(
    RuntimeError
):
    pass


class Client:
    def __init__(self):
        self.log = []
        self.last = {}

    def get(self, url):
        host = urllib.parse.urlsplit(
            url
        ).netloc

        elapsed = (
            time.monotonic()
            - self.last.get(
                host,
                0,
            )
        )

        time.sleep(
            max(
                0,
                1 - elapsed,
            )
        )

        self.last[
            host
        ] = time.monotonic()

        try:
            request = (
                urllib.request.Request(
                    url,
                    headers={
                        "User-Agent":
                            UA,
                        "Accept": (
                            "application/json,"
                            "text/html,"
                            "application/rss+xml,"
                            "text/calendar;q=0.9"
                        ),
                    },
                )
            )

            with urllib.request.urlopen(
                request,
                timeout=30,
            ) as response:
                body = response.read(
                    12_000_001
                )

                if (
                    len(body)
                    > 12_000_000
                ):
                    raise ValueError(
                        "Response exceeds "
                        "12 MB limit"
                    )

                self.log.append(
                    {
                        "url": url,
                        "final_url":
                            response.url,
                        "http_status":
                            response.status,
                        "content_type":
                            response.headers.get(
                                "Content-Type"
                            ),
                        "bytes":
                            len(body),
                        "sha256":
                            hashlib.sha256(
                                body
                            ).hexdigest(),
                    }
                )

                return (
                    body,
                    response.headers,
                )

        except urllib.error.HTTPError as exc:
            self.log.append(
                {
                    "url": url,
                    "http_status":
                        exc.code,
                }
            )

            if exc.code in {
                403,
                406,
                429,
            }:
                raise SourceBlockedError(
                    f"HTTP {exc.code}; "
                    "no authentication or "
                    "challenge bypass attempted"
                ) from exc

            if (
                exc.code == 404
                or 500
                <= exc.code
                <= 599
            ):
                raise SourceUnavailableError(
                    f"HTTP {exc.code}"
                ) from exc

            raise RuntimeError(
                f"HTTP {exc.code}"
            ) from exc

        except urllib.error.URLError as exc:
            raise SourceUnavailableError(
                str(exc.reason)
            ) from exc

    def page(self, url):
        body, _ = self.get(
            url
        )

        doc = html.fromstring(
            body
        )

        title = clean(
            " ".join(
                doc.xpath(
                    "//head/title/text()"
                )
            )
        )

        unavailable_titles = [
            "site unavailable",
            "access denied",
            "just a moment",
            "sign in",
        ]

        if any(
            phrase in title.lower()
            for phrase
            in unavailable_titles
        ):
            raise SourceBlockedError(
                "Unavailable/challenge/"
                f"login page: {title}"
            )

        return doc

    def json(self, url):
        body, headers = self.get(
            url
        )

        content_type = (
            headers.get(
                "Content-Type",
                "",
            ).lower()
        )

        if (
            "json"
            not in content_type
        ):
            raise ValueError(
                "Expected JSON, received "
                + (
                    content_type
                    or "unknown"
                )
            )

        return json.loads(
            body
        )


# --------------------------------------------------
# Normalized event
# --------------------------------------------------

def event(
    school,
    source,
    title,
    url,
    start=None,
    end=None,
    raw_date="",
    description="",
    **extra,
):
    return {
        "school": school,
        "source": source,
        "title": clean(
            title
        ),
        "url": url,
        "start_date": start,
        "end_date": end,
        "date_text": clean(
            raw_date
        ),
        "description": clean(
            description
        ),
        **extra,
    }


# --------------------------------------------------
# TMU
# --------------------------------------------------

def tmu_parse(data):
    if (
        not isinstance(
            data,
            dict,
        )
        or not isinstance(
            data.get("data"),
            list,
        )
        or "totalMatches"
        not in data
    ):
        raise ValueError(
            "TMU schema changed"
        )

    rows = []

    for item in data["data"]:

        def day(value):
            # Java Date.toString:
            # Wed Sep 09 11:00:00 EDT 2026

            parts = value.split()

            if len(parts) != 6:
                raise ValueError(
                    "Unknown TMU "
                    f"date format: {value}"
                )

            return dt.date(
                int(parts[5]),
                MONTHS[
                    parts[1].lower()
                ],
                int(parts[2]),
            ).isoformat()

        rows.append(
            event(
                "TMU",
                "tmu",
                item["title"],
                urllib.parse.urljoin(
                    "https://www.torontomu.ca",
                    item[
                        "page"
                    ].removeprefix(
                        "/content/ryerson"
                    ),
                ),
                day(
                    item["from"]
                ),
                day(
                    item["to"]
                ),
                (
                    item["from"]
                    + " — "
                    + item["to"]
                ),
                location=item.get(
                    "location"
                ),
                registration_url=item.get(
                    "website"
                ),
            )
        )

    return rows


def tmu(client):
    base = (
        "https://www.torontomu.ca/"
        "news-events/events/"
    )

    doc = client.page(
        base
    )

    scripts = "\n".join(
        doc.xpath(
            "//script[not(@src)]/"
            "text()"
        )
    )

    match = re.search(
        r'initStackComponent\(\{'
        r'.*?url:\s*"([^"]+)"',
        scripts,
        re.S,
    )

    if not match:
        raise ValueError(
            "TMU event stack "
            "configuration missing"
        )

    endpoint = (
        urllib.parse.urljoin(
            base,
            match[
                1
            ].removeprefix(
                "/content/ryerson"
            ),
        )
        + ".data.0.json"
    )

    data = client.json(
        endpoint
    )

    rows = tmu_parse(
        data
    )

    expected = (
        int(
            data[
                "totalMatches"
            ]
        )
        - int(
            data.get(
                "originalOffset",
                0,
            )
        )
    )

    if len(rows) < expected:
        raise ValueError(
            "TMU response truncated: "
            "pagination needs "
            "reinspection"
        )

    return rows


# --------------------------------------------------
# Waterloo / uOttawa listings
# --------------------------------------------------

def listing_parse(
    doc,
    school,
    source,
    base,
):
    if source == "waterloo":
        cards = doc.xpath(
            "//article["
            + cls(
                "card__teaser--event"
            )
            + "]"
        )

        nodes = [
            (
                card,
                card.xpath(
                    ".//h2["
                    + cls(
                        "card__title"
                    )
                    + "]/a"
                ),
                card.xpath(
                    ".//*["
                    + cls(
                        "uw-date"
                    )
                    + "]"
                ),
            )
            for card in cards
        ]

    else:
        cards = doc.xpath(
            "//div["
            + cls(
                "article-teaser__item-content"
            )
            + "]"
        )

        nodes = [
            (
                card,
                card.xpath(
                    ".//h2/a"
                ),
                card.xpath(
                    ".//div["
                    + cls(
                        "article-teaser__item-body-wordwrap"
                    )
                    + "]/strong"
                ),
            )
            for card in cards
        ]

    rows = []

    for card, links, labels in nodes:
        if (
            not links
            or not labels
        ):
            raise ValueError(
                f"{source}: event "
                "card schema changed"
            )

        label = text(
            labels[0]
        )

        start, end = dates(
            label
        )

        rows.append(
            event(
                school,
                source,
                text(
                    links[0]
                ),
                urllib.parse.urljoin(
                    base,
                    links[
                        0
                    ].get(
                        "href"
                    ),
                ),
                start,
                end,
                label,
                text(
                    card
                ),
            )
        )

    if not nodes:
        raise ValueError(
            f"{source}: no event "
            "cards; inspect empty-state "
            "or layout before treating "
            "as zero"
        )

    return rows


def listing(
    client,
    school,
    source,
    url,
    max_pages,
):
    rows = []
    visited = set()

    for _ in range(
        max_pages
    ):
        if url in visited:
            raise ValueError(
                "Pagination loop"
            )

        visited.add(
            url
        )

        doc = client.page(
            url
        )

        rows.extend(
            listing_parse(
                doc,
                school,
                source,
                url,
            )
        )

        next_links = doc.xpath(
            '//a[@rel="next"]/@href'
            " | "
            "//li["
            + cls(
                "pager__item--next"
            )
            + "]/a/@href"
        )

        if not next_links:
            return rows

        next_url = (
            urllib.parse.urljoin(
                url,
                next_links[0],
            )
        )

        current_host = (
            urllib.parse.urlsplit(
                url
            ).netloc
        )

        next_host = (
            urllib.parse.urlsplit(
                next_url
            ).netloc
        )

        if next_host != current_host:
            raise ValueError(
                "Cross-host pagination"
            )

        url = next_url

    raise ValueError(
        f"{source}: max-pages "
        "reached; scan incomplete"
    )


# --------------------------------------------------
# Carleton
# --------------------------------------------------

def carleton_parse(
    data,
    source="carleton",
):
    if (
        not isinstance(
            data,
            dict,
        )
        or not isinstance(
            data.get("posts"),
            list,
        )
    ):
        raise ValueError(
            "Carleton schema changed"
        )

    rows = []

    for item in data["posts"]:
        start = item.get(
            "cu_event_start_date"
        )

        end = item.get(
            "cu_event_end_date"
        )

        # Local campus datetime strings,
        # not UTC timestamps.
        for value in [
            start,
            end,
        ]:
            if value:
                dt.datetime.fromisoformat(
                    value
                )

        rows.append(
            event(
                "Carleton",
                source,
                item["title"],
                item["link"],
                (
                    start[:10]
                    if start
                    else None
                ),
                (
                    end[:10]
                    if end
                    else None
                ),
                clean(
                    start
                )
                + " — "
                + clean(
                    end
                ),
            )
        )

    total = int(
        data.get(
            "pagination",
            {},
        ).get(
            "total",
            len(rows),
        )
    )

    if total > len(rows):
        raise ValueError(
            "Carleton response "
            "truncated"
        )

    return rows


def carleton(client):
    return carleton_parse(
        client.json(
            "https://events.carleton.ca/"
            "wp-json/cutheme/v1/"
            "cu-calendar"
        )
    )


# --------------------------------------------------
# MLH / organizer pages
# --------------------------------------------------

DOMAINS = {
    "uofthacks.com":
        "U of T",

    "makeuoft.ca":
        "U of T",

    "hackthevalley.io":
        "U of T",

    "ellehacks.com":
        "York",

    "hackthenorth.com":
        "Waterloo",

    "deltahacks.com":
        "McMaster",

    "uottahack.ca":
        "uOttawa",

    "cuhacking.ca":
        "Carleton",
}


def mlh_parse(
    doc,
    source_url,
):
    cards = doc.xpath(
        '//*[@itemscope and '
        '@itemtype="https://schema.org/Event"]'
    )

    if not cards:
        raise ValueError(
            "MLH Event microdata missing"
        )

    output = []

    for card in cards:

        def prop(name):
            values = card.xpath(
                './/*[@itemprop="'
                + name
                + '"]/@content'
            )

            return (
                values[0]
                if values
                else None
            )

        url = prop(
            "url"
        )

        if not url:
            raise ValueError(
                "MLH event URL missing"
            )

        host = (
            urllib.parse.urlsplit(
                url
            )
            .netloc
            .lower()
            .removeprefix(
                "www."
            )
        )

        school = next(
            (
                school_name
                for domain, school_name
                in DOMAINS.items()
                if (
                    host == domain
                    or host.endswith(
                        "."
                        + domain
                    )
                )
            ),
            None,
        )

        if not school:
            continue

        title = card.xpath(
            ".//h4"
        )

        start = prop(
            "startDate"
        )

        end = prop(
            "endDate"
        )

        if (
            not start
            or not end
            or not title
        ):
            raise ValueError(
                "MLH event fields "
                "changed"
            )

        dt.date.fromisoformat(
            start[:10]
        )

        dt.date.fromisoformat(
            end[:10]
        )

        output.append(
            event(
                school,
                "mlh",
                text(
                    title[0]
                ),
                url,
                start[:10],
                end[:10],
                start
                + " — "
                + end,
                evidence_url=
                    source_url,
                date_precision=
                    "day",
                raw_start=start,
                raw_end=end,
            )
        )

    return output


def organizer_parse(
    doc,
    source,
    url,
):
    title = clean(
        " ".join(
            doc.xpath(
                "//head/title/text()"
            )
        )
    )

    if source == "uofthacks":
        labels = [
            text(node)
            for node
            in doc.xpath(
                "//span"
            )
            if re.fullmatch(
                MONTH
                + r" \d{4} "
                r"\| In-person event",
                text(node),
                re.I,
            )
        ]

        if not labels:
            raise ValueError(
                "UofTHacks hero month "
                "missing; recheck organizer"
            )

        match = re.search(
            r"("
            + MONTH
            + r") (\d{4})",
            labels[0],
            re.I,
        )

        return [
            event(
                "U of T",
                source,
                title,
                url,
                raw_date=
                    labels[0],
                date_precision=
                    "month",
                announced_month=(
                    f"{match[2]}-"
                    f"{MONTHS[match[1].lower()]:02d}"
                ),
                note=(
                    "Hero is month-only; "
                    "exact dates require "
                    "another source."
                ),
            )
        ]

    selector = (
        "//p"
        if source
        == "deltahacks"
        else "//main//span"
    )

    labels = [
        text(node)
        for node
        in doc.xpath(
            selector
        )
        if RANGE.search(
            text(node)
        )
    ]

    if not labels:
        raise ValueError(
            f"{source}: hero "
            "event date missing"
        )

    label = labels[0]

    start, end = dates(
        label
    )

    return [
        event(
            (
                "McMaster"
                if source
                == "deltahacks"
                else "Carleton"
            ),
            source,
            title,
            url,
            start,
            end,
            label,
            date_precision=
                "day",
        )
    ]


# --------------------------------------------------
# Providers
# --------------------------------------------------

PROVIDERS = {
    "tmu":
        lambda client, args:
            tmu(
                client
            ),

    "waterloo":
        lambda client, args:
            listing(
                client,
                "Waterloo",
                "waterloo",
                "https://uwaterloo.ca/"
                "events/events",
                args.max_pages,
            ),

    "uottawa":
        lambda client, args:
            listing(
                client,
                "uOttawa",
                "uottawa",
                "https://www.uottawa.ca/"
                "faculty-engineering/"
                "events-all",
                args.max_pages,
            ),

    "carleton":
        lambda client, args:
            carleton(
                client
            ),

    "carleton_scs":
        lambda client, args:
            carleton_parse(
                client.json(
                    "https://carleton.ca/"
                    "scs/wp-json/cutheme/"
                    "v1/cu-calendar"
                ),
                "carleton_scs",
            ),

    "uofthacks":
        lambda client, args:
            organizer_parse(
                client.page(
                    "https://uofthacks.com/"
                ),
                "uofthacks",
                "https://uofthacks.com/",
            ),

    "deltahacks":
        lambda client, args:
            organizer_parse(
                client.page(
                    "https://www.deltahacks.com/"
                ),
                "deltahacks",
                "https://www.deltahacks.com/",
            ),

    "cuhacking":
        lambda client, args:
            organizer_parse(
                client.page(
                    "https://cuhacking.ca/"
                ),
                "cuhacking",
                "https://cuhacking.ca/",
            ),

    "mlh":
        lambda client, args:
            mlh_parse(
                client.page(
                    f"https://www.mlh.com/"
                    f"seasons/{args.season}/"
                    f"events"
                ),
                f"https://www.mlh.com/"
                f"seasons/{args.season}/"
                f"events",
            ),
}


BROAD_FEED_SOURCES = {
    "tmu",
    "waterloo",
    "uottawa",
    "carleton",
    "carleton_scs",
}


# --------------------------------------------------
# Classification
# --------------------------------------------------

def relevant_event(
    item,
):
    haystack = (
        item.get(
            "title",
            ""
        )
        + " "
        + item.get(
            "description",
            ""
        )
    )

    return bool(
        HACK.search(
            haystack
        )
    )


def classify_date(
    item,
    as_of,
):
    start = item.get(
        "start_date"
    )

    end = (
        item.get(
            "end_date"
        )
        or start
    )

    today = (
        as_of.isoformat()
    )

    if (
        end
        and end < today
    ):
        return "past"

    if (
        start
        and start > today
    ):
        return "upcoming"

    if (
        start
        and end
    ):
        return (
            "ongoing_or_today"
        )

    return "needs_date_review"


# --------------------------------------------------
# Persistent discovery dedupe
# --------------------------------------------------

def load_state():
    if not STATE_FILE.exists():
        return {
            "initialized": False,
            "events": {},
        }

    try:
        with STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as file:
            state = json.load(
                file
            )

    except Exception:
        return {
            "initialized": False,
            "events": {},
        }

    if not isinstance(
        state,
        dict,
    ):
        return {
            "initialized": False,
            "events": {},
        }

    state.setdefault(
        "initialized",
        False,
    )

    state.setdefault(
        "events",
        {},
    )

    return state


def save_state(state):
    with STATE_FILE.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            state,
            file,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        file.write(
            "\n"
        )


def event_key(item):
    raw = "|".join(
        [
            str(
                item.get(
                    "school",
                    "",
                )
            ).strip().lower(),
            str(
                item.get(
                    "source",
                    "",
                )
            ).strip().lower(),
            str(
                item.get(
                    "url",
                    "",
                )
            ).strip(),
            str(
                item.get(
                    "start_date",
                    "",
                )
            ),
            str(
                item.get(
                    "end_date",
                    "",
                )
            ),
        ]
    )

    return hashlib.sha256(
        raw.encode(
            "utf-8"
        )
    ).hexdigest()


def state_record(item):
    return {
        "school":
            item.get(
                "school"
            ),

        "source":
            item.get(
                "source"
            ),

        "title":
            item.get(
                "title"
            ),

        "url":
            item.get(
                "url"
            ),

        "start_date":
            item.get(
                "start_date"
            ),

        "end_date":
            item.get(
                "end_date"
            ),

        "last_seen":
            dt.datetime.now(
                dt.timezone.utc
            ).isoformat(),
    }


# --------------------------------------------------
# Discord
# --------------------------------------------------

def source_label(source):
    labels = {
        "tmu":
            "TMU Events",

        "waterloo":
            "Waterloo Events",

        "uottawa":
            "uOttawa Engineering Events",

        "carleton":
            "Carleton Events",

        "carleton_scs":
            "Carleton SCS",

        "uofthacks":
            "UofTHacks",

        "deltahacks":
            "DeltaHacks",

        "cuhacking":
            "cuHacking",

        "mlh":
            "MLH",
    }

    return labels.get(
        source,
        source,
    )


def send_discord(item):
    if not DISCORD_WEBHOOK_URL:
        print(
            "DISCORD SKIPPED: "
            "DISCORD_WEBHOOK_URL "
            "not configured.",
            file=sys.stderr,
        )

        return False

    start = (
        item.get(
            "start_date"
        )
        or "Unknown"
    )

    end = (
        item.get(
            "end_date"
        )
        or start
    )

    if (
        start != "Unknown"
        and end
        and end != start
    ):
        date_display = (
            f"{start} → {end}"
        )
    else:
        date_display = start

    registration_url = (
        item.get(
            "registration_url"
        )
    )

    lines = [
        f"**School:** "
        f"{item['school']}",

        f"**Date:** "
        f"{date_display}",

        f"**Source:** "
        f"{source_label(item['source'])}",
    ]

    if item.get(
        "location"
    ):
        lines.append(
            f"**Location:** "
            f"{item['location']}"
        )

    if registration_url:
        lines.append(
            f"**Registration:** "
            f"{registration_url}"
        )
    else:
        lines.append(
            "**Registration:** "
            "🟡 Not found — verify "
            "registration is still open."
        )

    payload = {
        "username":
            "Hackathon Monitor",

        "embeds": [
            {
                "title": (
                    "🎓 NEW UNIVERSITY "
                    "HACKATHON / COMPETITION"
                ),

                "description": (
                    f"**{item['title']}**"
                    "\n\n"
                    + "\n".join(
                        lines
                    )
                ),

                "url":
                    item["url"],

                "footer": {
                    "text": (
                        "University-wide "
                        "discovery source"
                    )
                },
            }
        ],
    }

    body = json.dumps(
        payload
    ).encode(
        "utf-8"
    )

    request = (
        urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=body,
            headers={
                "Content-Type":
                    "application/json",
                "User-Agent":
                    UA,
            },
            method="POST",
        )
    )

    with urllib.request.urlopen(
        request,
        timeout=30,
    ) as response:
        if (
            response.status
            < 200
            or response.status
            >= 300
        ):
            raise RuntimeError(
                "Discord returned "
                f"HTTP {response.status}"
            )

    print(
        "DISCORD: "
        f"{item['school']} — "
        f"{item['title']}",
        file=sys.stderr,
    )

    return True


# --------------------------------------------------
# Notification processing
# --------------------------------------------------

def process_notifications(
    events,
    notify,
):
    state = load_state()

    current_keys = {}

    alert_candidates = []

    for item in events:
        # Only send confirmed future,
        # explicitly dated events.
        if (
            item.get(
                "status"
            )
            != "upcoming"
        ):
            continue

        key = event_key(
            item
        )

        current_keys[
            key
        ] = state_record(
            item
        )

        if (
            key
            not in state[
                "events"
            ]
        ):
            alert_candidates.append(
                (
                    key,
                    item,
                )
            )

    if not state[
        "initialized"
    ]:
        for key, item in alert_candidates:
            state[
                "events"
            ][
                key
            ] = state_record(
                item
            )

        state[
            "initialized"
        ] = True

        state[
            "last_run"
        ] = dt.datetime.now(
            dt.timezone.utc
        ).isoformat()

        save_state(
            state
        )

        print(
            "DISCOVERY BASELINE: "
            f"{len(alert_candidates)} "
            "future relevant events "
            "stored; no Discord alerts.",
            file=sys.stderr,
        )

        return {
            "baseline":
                len(
                    alert_candidates
                ),

            "new":
                0,

            "alerts":
                0,
        }

    alerts = 0

    for key, item in alert_candidates:
        if notify:
            send_discord(
                item
            )

            alerts += 1

        state[
            "events"
        ][
            key
        ] = state_record(
            item
        )

    # Refresh last-seen records for
    # currently visible events.
    for key, record in (
        current_keys.items()
    ):
        if (
            key
            in state[
                "events"
            ]
        ):
            state[
                "events"
            ][
                key
            ][
                "last_seen"
            ] = record[
                "last_seen"
            ]

    state[
        "last_run"
    ] = dt.datetime.now(
        dt.timezone.utc
    ).isoformat()

    save_state(
        state
    )

    return {
        "baseline":
            0,

        "new":
            len(
                alert_candidates
            ),

        "alerts":
            alerts,
    }


# --------------------------------------------------
# Main
# --------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--sources",
        nargs="+",
        choices=PROVIDERS,
        default=list(
            PROVIDERS
        ),
    )

    parser.add_argument(
        "--as-of",
        type=dt.date.fromisoformat,
        default=dt.datetime.now(
            TZ
        ).date(),
    )

    parser.add_argument(
        "--season",
        type=int,
        default=2027,
        help=(
            "MLH season, not "
            "calendar year; "
            "update annually"
        ),
    )

    parser.add_argument(
        "--max-pages",
        type=int,
        default=40,
    )

    parser.add_argument(
        "--include-past",
        action="store_true",
    )

    parser.add_argument(
        "--all-events",
        action="store_true",
        help=(
            "Diagnostic: include "
            "non-competition campus "
            "events"
        ),
    )

    parser.add_argument(
        "--notify",
        action="store_true",
        help=(
            "Send newly discovered "
            "future events to Discord. "
            "The first run establishes "
            "a baseline without alerts."
        ),
    )

    args = parser.parse_args()

    client = Client()

    statuses = []
    events = []

    for source in args.sources:
        try:
            rows = (
                PROVIDERS[
                    source
                ](
                    client,
                    args,
                )
            )

            selected = []

            for item in rows:
                if (
                    source
                    in BROAD_FEED_SOURCES
                    and not args.all_events
                    and not relevant_event(
                        item
                    )
                ):
                    continue

                state = classify_date(
                    item,
                    args.as_of,
                )

                item[
                    "status"
                ] = state

                if (
                    state != "past"
                    or args.include_past
                ):
                    selected.append(
                        item
                    )

            events.extend(
                selected
            )

            statuses.append(
                {
                    "source":
                        source,

                    "status":
                        "SUCCEEDED",

                    "records_scanned":
                        len(
                            rows
                        ),

                    "records_returned":
                        len(
                            selected
                        ),
                }
            )

            print(
                f"SUCCEEDED: "
                f"{source}: "
                f"{len(rows)} scanned, "
                f"{len(selected)} returned",
                file=sys.stderr,
            )

        except SourceBlockedError as exc:
            statuses.append(
                {
                    "source":
                        source,

                    "status":
                        "BLOCKED",

                    "error":
                        str(
                            exc
                        ),
                }
            )

            print(
                f"BLOCKED: "
                f"{source}: "
                f"{exc}",
                file=sys.stderr,
            )

        except SourceUnavailableError as exc:
            statuses.append(
                {
                    "source":
                        source,

                    "status":
                        "UNAVAILABLE",

                    "error":
                        str(
                            exc
                        ),
                }
            )

            print(
                f"UNAVAILABLE: "
                f"{source}: "
                f"{exc}",
                file=sys.stderr,
            )

        except Exception as exc:
            statuses.append(
                {
                    "source":
                        source,

                    "status":
                        "FAILED",

                    "error":
                        str(
                            exc
                        ),
                }
            )

            print(
                f"FAILED: "
                f"{source}: "
                f"{exc}",
                file=sys.stderr,
            )

    # Preserve conflicting assertions
    # from different sources.
    #
    # Only remove exact within-source
    # duplicates.
    unique = {
        (
            item[
                "source"
            ],
            item[
                "url"
            ],
            item.get(
                "start_date"
            ),
            item.get(
                "end_date"
            ),
        ):
            item

        for item in events
    }

    events = list(
        unique.values()
    )

    notification_result = (
        process_notifications(
            events,
            args.notify,
        )
    )

    result = {
        "checked_at":
            dt.datetime.now(
                dt.timezone.utc
            ).isoformat(),

        "as_of":
            args.as_of.isoformat(),

        "timezone":
            "America/Toronto",

        "sources":
            statuses,

        "events":
            events,

        "notifications":
            notification_result,

        "requests":
            client.log,
    }

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )

    # BLOCKED and UNAVAILABLE sources
    # are reported but do not fail the
    # GitHub Action.
    #
    # Only genuine parser/code failures
    # return non-zero.
    return (
        1
        if any(
            item[
                "status"
            ]
            == "FAILED"
            for item
            in statuses
        )
        else 0
    )


if __name__ == "__main__":
    sys.exit(
        main()
    )

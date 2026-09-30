import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dateparser.search import search_dates


CLUBS_FILE = Path("university_club_watch.json")
STATE_FILE = Path("club_discovery_state.json")

DISCORD_WEBHOOK_URL = os.environ.get(
    "DISCORD_WEBHOOK_URL"
)

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


DISCOVERY_PATTERNS = [
    r"\bhackathon\b",
    r"\bhack[\s-]?athon\b",
    r"\bdatathon\b",
    r"\bmakeathon\b",
    r"\bdesignathon\b",
    r"\bbuildathon\b",
    r"\bcodeathon\b",
    r"\bgame\s*jam\b",
    r"\bctf\b",
    r"\bcapture\s+the\s+flag\b",
    r"\bcoding\s+competition\b",
    r"\bcoding\s+contest\b",
    r"\bprogramming\s+competition\b",
    r"\bprogramming\s+contest\b",
    r"\bcompetitive\s+programming\b",
    r"\brobotics\s+competition\b",
    r"\brobotics\s+challenge\b",
    r"\bengineering\s+competition\b",
    r"\bengineering\s+challenge\b",
    r"\binnovation\s+challenge\b",
    r"\bai\s+competition\b",
    r"\bai\s+challenge\b",
    r"\bml\s+competition\b",
    r"\bml\s+challenge\b",
    r"\bdata\s+competition\b",
    r"\bdata\s+challenge\b",
]


REGISTRATION_PATTERNS = [
    r"\bapply\b",
    r"\bapplications?\b",
    r"\bregister\b",
    r"\bregistration\b",
    r"\bdeadline\b",
    r"\bsign\s*up\b",
    r"\bsignup\b",
]


EVENT_PLATFORM_HOSTS = [
    "luma.com",
    "lu.ma",
    "devpost.com",
    "eventbrite.",
    "itch.io",
    "forms.gle",
    "docs.google.com",
]


SKIP_HOSTS = [
    "instagram.com",
    "linkedin.com",
    "facebook.com",
    "tiktok.com",
    "x.com",
    "twitter.com",
]


GENERIC_DIRECTORY_PATTERNS = [
    "yourtmsu.ca/groups/student-groups",
]


class SourceBlockedError(Exception):
    pass


class SourceUnavailableError(Exception):
    pass


def load_clubs():
    with CLUBS_FILE.open(
        "r",
        encoding="utf-8",
    ) as f:
        data = json.load(f)

    return [
        club
        for club in data["clubs"]
        if club.get(
            "active_watch",
            True,
        )
    ]


def load_state():
    if not STATE_FILE.exists():
        return {}

    try:
        with STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as f:
            return json.load(f)

    except Exception:
        return {}


def save_state(state):
    with STATE_FILE.open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            state,
            f,
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )

        f.write("\n")


def clean_text(value):
    return re.sub(
        r"\s+",
        " ",
        str(value or ""),
    ).strip()


def matches_discovery(text):
    return any(
        re.search(
            pattern,
            text,
            re.IGNORECASE,
        )
        for pattern
        in DISCOVERY_PATTERNS
    )


def matches_registration(text):
    return any(
        re.search(
            pattern,
            text,
            re.IGNORECASE,
        )
        for pattern
        in REGISTRATION_PATTERNS
    )


def is_event_platform(url):
    lower = url.lower()

    return any(
        host in lower
        for host in EVENT_PLATFORM_HOSTS
    )


def should_skip_social(url):
    try:
        host = (
            urlparse(url)
            .netloc
            .lower()
        )

    except Exception:
        return True

    return any(
        skip in host
        for skip in SKIP_HOSTS
    )


def is_generic_directory(url):
    lower = url.lower()

    return any(
        pattern in lower
        for pattern
        in GENERIC_DIRECTORY_PATTERNS
    )


def fetch(url):
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=20,
            allow_redirects=True,
        )

    except requests.exceptions.SSLError as exc:
        raise SourceUnavailableError(
            f"{url} -> SSL unavailable: {exc}"
        ) from exc

    except requests.exceptions.ConnectionError as exc:
        raise SourceUnavailableError(
            f"{url} -> connection unavailable: {exc}"
        ) from exc

    except requests.exceptions.Timeout as exc:
        raise SourceUnavailableError(
            f"{url} -> timeout"
        ) from exc

    status = response.status_code

    if status in {
        403,
        406,
        429,
    }:
        raise SourceBlockedError(
            f"{url} -> HTTP {status}"
        )

    if status == 404:
        raise SourceUnavailableError(
            f"{url} -> HTTP 404"
        )

    if 500 <= status <= 599:
        raise SourceUnavailableError(
            f"{url} -> HTTP {status}"
        )

    response.raise_for_status()

    return (
        response.text,
        response.url,
    )


def get_candidate_urls(club):
    urls = []

    monitor_url = club.get(
        "monitor_url"
    )

    if monitor_url:
        urls.append(
            monitor_url
        )

    for backup in club.get(
        "backup_urls",
        [],
    ):
        if backup:
            urls.append(
                backup
            )

    if not urls:
        directory_url = club.get(
            "directory_url"
        )

        if directory_url:
            urls.append(
                directory_url
            )

    urls = list(
        dict.fromkeys(
            urls
        )
    )

    return [
        url
        for url in urls
        if (
            not should_skip_social(
                url
            )
            and not is_generic_directory(
                url
            )
        )
    ]


def extract_signals(
    html,
    base_url,
):
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    signals = set()

    for tag in soup.find_all(
        [
            "h1",
            "h2",
            "h3",
            "h4",
            "p",
            "li",
            "span",
        ]
    ):
        text = clean_text(
            tag.get_text(
                " ",
                strip=True,
            )
        )

        if not text:
            continue

        if len(text) > 300:
            continue

        if matches_discovery(
            text
        ):
            signals.add(
                f"TEXT|{text}"
            )

    for link in soup.find_all(
        "a",
        href=True,
    ):
        label = clean_text(
            link.get_text(
                " ",
                strip=True,
            )
        )

        url = urljoin(
            base_url,
            link["href"],
        )

        combined = (
            f"{label} {url}"
        )

        if (
            matches_discovery(
                combined
            )
            or (
                is_event_platform(
                    url
                )
                and matches_registration(
                    combined
                )
            )
        ):
            signals.add(
                f"LINK|{label}|{url}"
            )

    return sorted(
        signals
    )


def fingerprint(signals):
    joined = "\n".join(
        signals
    )

    return hashlib.sha256(
        joined.encode(
            "utf-8"
        )
    ).hexdigest()


def new_signals(
    old_signals,
    current_signals,
):
    old_set = set(
        old_signals or []
    )

    return [
        signal
        for signal
        in current_signals
        if signal not in old_set
    ]


def signal_is_high_value(signal):
    if signal.startswith(
        "LINK|"
    ):
        return True

    return (
        matches_discovery(
            signal
        )
        and matches_registration(
            signal
        )
    )


# --------------------------------------------------
# Date detection
# --------------------------------------------------

def safe_date(year, month, day):
    try:
        return datetime(
            int(year),
            int(month),
            int(day),
            tzinfo=timezone.utc,
        ).date()

    except (
        TypeError,
        ValueError,
    ):
        return None


def extract_explicit_date(text):
    """
    Extract an explicitly written calendar date.

    Important:
    - Requires a visible 4-digit year.
    - Does not infer a missing year.
    - Supports several languages.
    """

    if not re.search(
        r"\b20\d{2}\b",
        text,
    ):
        return None

    # Numeric year-first fallback:
    # 2026-09-16
    # 2026/09/16
    # 2026.09.16
    match = re.search(
        r"\b(20\d{2})[-/.]"
        r"(\d{1,2})[-/.]"
        r"(\d{1,2})\b",
        text,
    )

    if match:
        return safe_date(
            match.group(1),
            match.group(2),
            match.group(3),
        )

    # Numeric day-first:
    # 16/09/2026
    # 16.09.2026
    # 16-09-2026
    match = re.search(
        r"\b(\d{1,2})[-/.]"
        r"(\d{1,2})[-/.]"
        r"(20\d{2})\b",
        text,
    )

    if match:
        return safe_date(
            match.group(3),
            match.group(2),
            match.group(1),
        )

    try:
        found = search_dates(
            text,
            languages=[
                "en",
                "fr",
                "es",
                "pt",
                "ko",
                "zh",
                "ja",
            ],
            settings={
                "STRICT_PARSING": True,
                "DATE_ORDER": "DMY",
            },
        )

    except Exception:
        found = None

    if not found:
        return None

    for matched_text, parsed in found:
        # The actual matched phrase must itself
        # contain an explicit year.
        if not re.search(
            r"\b20\d{2}\b",
            matched_text,
        ):
            continue

        return parsed.date()

    return None


def signal_is_past_event(signal):
    explicit_date = extract_explicit_date(
        signal
    )

    if not explicit_date:
        return False

    today = datetime.now(
        timezone.utc
    ).date()

    return explicit_date < today


def format_signal(signal):
    parts = signal.split(
        "|"
    )

    if (
        parts[0] == "LINK"
        and len(parts) >= 3
    ):
        label = (
            parts[1]
            or "New event link"
        )

        return (
            f"• **{label}** — "
            f"{parts[2]}"
        )

    if len(parts) >= 2:
        return (
            "• "
            + "|".join(
                parts[1:]
            )
        )

    return (
        f"• {signal}"
    )


def send_discord(
    club,
    signals,
):
    if not DISCORD_WEBHOOK_URL:
        print(
            "DISCORD SKIPPED: "
            "DISCORD_WEBHOOK_URL "
            "not configured."
        )
        return False

    lines = [
        format_signal(
            signal
        )
        for signal in signals[:6]
    ]

    if len(signals) > 6:
        lines.append(
            f"• +{len(signals) - 6} "
            f"more new signals"
        )

    payload = {
        "username": (
            "Hackathon Monitor"
        ),
        "embeds": [
            {
                "title": (
                    "🔎 POSSIBLE NEW "
                    "HACKATHON / COMPETITION"
                ),
                "description": (
                    f"**{club['name']}** "
                    f"({club['school']}) "
                    f"added new public "
                    f"event-related information."
                    f"\n\n"
                    + "\n".join(
                        lines
                    )
                ),
                "footer": {
                    "text": (
                        "Discovery alert — "
                        "verify that this is a "
                        "future event and that "
                        "registration is open "
                        "before treating it as "
                        "confirmed."
                    )
                },
            }
        ],
    }

    response = requests.post(
        DISCORD_WEBHOOK_URL,
        json=payload,
        timeout=30,
    )

    response.raise_for_status()

    print(
        "DISCORD: discovery "
        "alert sent."
    )

    return True


def main():
    clubs = load_clubs()
    state = load_state()

    print(
        f"Loaded {len(clubs)} "
        f"club discovery entries."
    )

    baseline = 0
    unchanged = 0
    changed = 0
    blocked = 0
    unavailable = 0
    failed = 0
    skipped = 0
    alerts = 0
    past_suppressed = 0

    for index, club in enumerate(
        clubs,
        start=1,
    ):
        club_id = club["id"]

        print(
            f"\n[{index}/{len(clubs)}] "
            f"{club['school']} — "
            f"{club['name']}"
        )

        urls = get_candidate_urls(
            club
        )

        if not urls:
            skipped += 1

            print(
                "SKIPPED: "
                "no automatable event URL."
            )

            continue

        combined_signals = set()
        successful_urls = []

        blocked_urls = []
        unavailable_urls = []
        unexpected_errors = []

        for url in urls:
            try:
                html, final_url = fetch(
                    url
                )

                successful_urls.append(
                    final_url
                )

                signals = extract_signals(
                    html,
                    final_url,
                )

                combined_signals.update(
                    signals
                )

            except SourceBlockedError as exc:
                blocked_urls.append(
                    str(exc)
                )

            except SourceUnavailableError as exc:
                unavailable_urls.append(
                    str(exc)
                )

            except Exception as exc:
                unexpected_errors.append(
                    f"{url} -> {exc}"
                )

        if successful_urls:
            for item in blocked_urls:
                print(
                    f"  URL BLOCKED: "
                    f"{item}"
                )

            for item in unavailable_urls:
                print(
                    f"  URL UNAVAILABLE: "
                    f"{item}"
                )

            for item in unexpected_errors:
                print(
                    f"  URL FAILED: "
                    f"{item}"
                )

        else:
            if unexpected_errors:
                failed += 1

                print(
                    "FAILED: "
                    + "; ".join(
                        unexpected_errors
                    )
                )

                continue

            if blocked_urls:
                blocked += 1

                print(
                    "BLOCKED: "
                    + "; ".join(
                        blocked_urls
                    )
                )

                continue

            if unavailable_urls:
                unavailable += 1

                print(
                    "UNAVAILABLE: "
                    + "; ".join(
                        unavailable_urls
                    )
                )

                continue

            failed += 1

            print(
                "FAILED: no usable "
                "candidate URL."
            )

            continue

        signals = sorted(
            combined_signals
        )

        current = {
            "urls": successful_urls,
            "fingerprint": (
                fingerprint(
                    signals
                )
            ),
            "signals": signals,
        }

        previous = state.get(
            club_id
        )

        if not previous:
            baseline += 1

            state[
                club_id
            ] = current

            print(
                f"BASELINE: "
                f"{len(signals)} "
                f"signals saved."
            )

            continue

        if (
            previous.get(
                "fingerprint"
            )
            == current[
                "fingerprint"
            ]
        ):
            unchanged += 1

            state[
                club_id
            ] = current

            print(
                f"UNCHANGED: "
                f"{len(signals)} "
                f"signals."
            )

            continue

        changed += 1

        additions = new_signals(
            previous.get(
                "signals",
                [],
            ),
            signals,
        )

        high_value = [
            signal
            for signal in additions
            if signal_is_high_value(
                signal
            )
        ]

        alertworthy = []

        for signal in high_value:
            explicit_date = (
                extract_explicit_date(
                    signal
                )
            )

            if (
                explicit_date
                and signal_is_past_event(
                    signal
                )
            ):
                past_suppressed += 1

                print(
                    "  SUPPRESSED PAST: "
                    f"{explicit_date.isoformat()} "
                    f"| {signal[:250]}"
                )

                continue

            alertworthy.append(
                signal
            )

        print(
            f"CHANGED: "
            f"{len(additions)} new, "
            f"{len(high_value)} "
            f"high-value, "
            f"{len(alertworthy)} "
            f"alertworthy."
        )

        for signal in additions[
            :10
        ]:
            print(
                f"  NEW: "
                f"{signal[:300]}"
            )

        if alertworthy:
            try:
                if send_discord(
                    club,
                    alertworthy,
                ):
                    alerts += 1

            except Exception as exc:
                print(
                    "DISCORD FAILED: "
                    f"{exc}"
                )

                continue

        state[
            club_id
        ] = current

    save_state(state)

    print()

    print(
        f"Finished: "
        f"{baseline} baseline, "
        f"{unchanged} unchanged, "
        f"{changed} changed, "
        f"{blocked} blocked, "
        f"{unavailable} unavailable, "
        f"{failed} failed, "
        f"{skipped} skipped, "
        f"{past_suppressed} past suppressed, "
        f"{alerts} Discord alerts."
    )


if __name__ == "__main__":
    main()

import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup


CLUBS_FILE = Path("university_club_watch.json")
PUBLIC_SOURCES_FILE = Path("public_organizer_sources.json")
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


# --------------------------------------------------
# Geographic policy
# --------------------------------------------------

TORONTO_GTA_SCHOOLS = {
    "TMU",
    "U of T",
    "York",
}


# --------------------------------------------------
# Core events
#
# These are worth alerting for regardless of which
# monitored university hosts them.
# --------------------------------------------------

CORE_EVENT_PATTERNS = [
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
    r"\bmachine\s+learning\s+competition\b",
    r"\bmachine\s+learning\s+challenge\b",
    r"\bdata\s+competition\b",
    r"\bdata\s+challenge\b",

    r"\bcase\s+competition\b",
]


# --------------------------------------------------
# Extra technical events
#
# These are useful, but only worth notifying about
# when hosted in Toronto/GTA.
# --------------------------------------------------

LOCAL_TECH_EVENT_PATTERNS = [
    r"\bcyber\s+summit\b",
    r"\bcybersecurity\s+summit\b",
    r"\btech(?:nology)?\s+summit\b",

    r"\bdeveloper\s+conference\b",
    r"\bengineering\s+conference\b",
    r"\bai\s+conference\b",
    r"\bmachine\s+learning\s+conference\b",

    r"\bcyber\s+conference\b",
    r"\bcybersecurity\s+conference\b",

    r"\btechnical\s+conference\b",
    r"\btech\s+conference\b",

    r"\bdeveloper\s+summit\b",
    r"\bengineering\s+summit\b",
    r"\bai\s+summit\b",
]


DISCOVERY_PATTERNS = (
    CORE_EVENT_PATTERNS
    + LOCAL_TECH_EVENT_PATTERNS
)


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

TRACKING_QUERY_KEYS = {
    "fbclid", "gclid", "dclid", "msclkid", "igshid",
    "mc_cid", "mc_eid", "ref_src",
}


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

    with PUBLIC_SOURCES_FILE.open("r", encoding="utf-8") as f:
        public_sources = json.load(f)["sources"]

    return [
        club
        for club in data["clubs"] + public_sources
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


def matches_patterns(
    text,
    patterns,
):
    return any(
        re.search(
            pattern,
            text,
            re.IGNORECASE,
        )
        for pattern in patterns
    )


def matches_discovery(text):
    return matches_patterns(
        text,
        DISCOVERY_PATTERNS,
    )


def matches_core_event(text):
    return matches_patterns(
        text,
        CORE_EVENT_PATTERNS,
    )


def matches_local_tech_event(text):
    return matches_patterns(
        text,
        LOCAL_TECH_EVENT_PATTERNS,
    )


def matches_registration(text):
    return any(
        re.search(
            pattern,
            text,
            re.IGNORECASE,
        )
        for pattern in REGISTRATION_PATTERNS
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
        for pattern in GENERIC_DIRECTORY_PATTERNS
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

        # A tag wrapping an event link is the same candidate. Keep the link,
        # whose target provides a durable identity across copy changes.
        linked_candidate = False
        for link in tag.find_all("a", href=True):
            target = urljoin(base_url, link["href"])
            label = clean_text(link.get_text(" ", strip=True))
            combined = f"{label} {target}"
            if matches_discovery(combined) or (
                is_event_platform(target) and matches_registration(combined)
            ):
                linked_candidate = True
                break
        if linked_candidate:
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
        for signal in current_signals
        if signal not in old_set
    ]


def canonical_event_url(url):
    """Remove URL decoration without discarding event-identifying query fields."""
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return None
        host = parts.hostname.lower().rstrip(".")
        port = parts.port
        netloc = host if port is None or (parts.scheme.lower(), port) in {
            ("http", 80), ("https", 443),
        } else f"{host}:{port}"
        query = [
            (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in TRACKING_QUERY_KEYS
        ]
        path = parts.path.rstrip("/") or "/"
        return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(query), ""))
    except ValueError:
        return None


def normalized_identity_text(value):
    return clean_text(unicodedata.normalize("NFKC", value)).casefold()


def signal_identity(club_id, signal):
    """Use a target URL when possible; otherwise use the exact normalized title/date."""
    kind, _, value = signal.partition("|")
    if kind == "LINK":
        label, _, url = value.partition("|")
        canonical = canonical_event_url(url)
        if canonical:
            return f"url:{canonical}"
        title = label
    elif kind == "TEXT":
        title = value
    else:
        title = signal
    date = extract_explicit_date(title)
    return "text:{}:{}:{}".format(
        normalized_identity_text(club_id),
        normalized_identity_text(title),
        date.isoformat() if date else "",
    )


def signal_identities(club_id, signals):
    """Associate a text mention with its linked event when the title is clear."""
    identities = {signal: signal_identity(club_id, signal) for signal in signals}
    linked_titles = []
    for signal in signals:
        if not signal.startswith("LINK|"):
            continue
        label = normalized_identity_text(signal.split("|", 2)[1])
        if len(label) >= 12 and matches_discovery(label):
            linked_titles.append((label, identities[signal]))
    for signal in signals:
        if not signal.startswith("TEXT|"):
            continue
        title = normalized_identity_text(signal[5:])
        matches = {event_id for label, event_id in linked_titles if label in title}
        if len(matches) == 1:
            identities[signal] = matches.pop()
    return identities


def signal_is_high_value(
    club,
    signal,
):
    """
    Alert policy:

    1. Hackathons / competitions:
       alert regardless of monitored school.

    2. Broader technical conferences/summits:
       alert only for Toronto/GTA schools.

    3. Generic application/registration links:
       preserve the existing false-positive-friendly
       behavior only for Toronto/GTA schools.
    """

    school = club.get(
        "school",
        ""
    )

    is_toronto_gta = (
        school
        in TORONTO_GTA_SCHOOLS
    )

    if matches_core_event(
        signal
    ):
        return True

    if (
        club.get("core_event_organizer")
        and signal.startswith("LINK|")
        and any(host in signal.lower() for host in EVENT_PLATFORM_HOSTS)
        and matches_registration(signal)
    ):
        return True

    if (
        is_toronto_gta
        and matches_local_tech_event(
            signal
        )
    ):
        return True

    if (
        is_toronto_gta
        and signal.startswith(
            "LINK|"
        )
    ):
        return True

    return False


# --------------------------------------------------
# Explicit date detection
# --------------------------------------------------

MONTH_NAMES = {
    "jan": 1,
    "january": 1,
    "janeiro": 1,
    "enero": 1,
    "janvier": 1,

    "feb": 2,
    "february": 2,
    "fevereiro": 2,
    "febrero": 2,
    "fevrier": 2,

    "mar": 3,
    "march": 3,
    "marco": 3,
    "marzo": 3,
    "mars": 3,

    "apr": 4,
    "april": 4,
    "abril": 4,
    "avr": 4,
    "avril": 4,

    "may": 5,
    "maio": 5,
    "mayo": 5,
    "mai": 5,

    "jun": 6,
    "june": 6,
    "junho": 6,
    "junio": 6,
    "juin": 6,

    "jul": 7,
    "july": 7,
    "julho": 7,
    "julio": 7,
    "juillet": 7,

    "aug": 8,
    "august": 8,
    "agosto": 8,
    "aout": 8,

    "sep": 9,
    "sept": 9,
    "september": 9,
    "set": 9,
    "setembro": 9,
    "septiembre": 9,
    "septembre": 9,

    "oct": 10,
    "october": 10,
    "out": 10,
    "outubro": 10,
    "octubre": 10,
    "octobre": 10,

    "nov": 11,
    "november": 11,
    "novembro": 11,
    "noviembre": 11,
    "novembre": 11,

    "dec": 12,
    "december": 12,
    "dez": 12,
    "dezembro": 12,
    "diciembre": 12,
    "decembre": 12,
}


def normalize_word(value):
    value = value.strip(
        " .,;"
    ).lower()

    value = unicodedata.normalize(
        "NFKD",
        value,
    )

    return "".join(
        char
        for char in value
        if not unicodedata.combining(
            char
        )
    )


def month_number(value):
    return MONTH_NAMES.get(
        normalize_word(
            value
        )
    )


def safe_date(
    year,
    month,
    day,
):
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
    if not re.search(
        r"20\d{2}",
        text,
    ):
        return None

    # Korean
    match = re.search(
        r"(20\d{2})\s*년\s*"
        r"(\d{1,2})\s*월\s*"
        r"(\d{1,2})\s*일",
        text,
    )

    if match:
        return safe_date(
            match.group(1),
            match.group(2),
            match.group(3),
        )

    # Chinese / Japanese
    match = re.search(
        r"(20\d{2})\s*年\s*"
        r"(\d{1,2})\s*月\s*"
        r"(\d{1,2})\s*日",
        text,
    )

    if match:
        return safe_date(
            match.group(1),
            match.group(2),
            match.group(3),
        )

    # 2026-09-16
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

    # 16/09/2026
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

    # 16. Sept. 2026
    # 16 de set. de 2026
    # 16 septembre 2026
    match = re.search(
        r"\b(\d{1,2})"
        r"(?:st|nd|rd|th)?"
        r"\.?"
        r"\s+"
        r"(?:de\s+)?"
        r"([A-Za-zÀ-ÿ]+\.?)"
        r"\s+"
        r"(?:de\s+)?"
        r"(20\d{2})\b",
        text,
        re.IGNORECASE,
    )

    if match:
        month = month_number(
            match.group(2)
        )

        if month:
            return safe_date(
                match.group(3),
                month,
                match.group(1),
            )

    # September 16, 2026
    match = re.search(
        r"\b([A-Za-zÀ-ÿ]+\.?)"
        r"\s+"
        r"(\d{1,2})"
        r"(?:st|nd|rd|th)?"
        r",?"
        r"\s+"
        r"(20\d{2})\b",
        text,
        re.IGNORECASE,
    )

    if match:
        month = month_number(
            match.group(1)
        )

        if month:
            return safe_date(
                match.group(3),
                month,
                match.group(2),
            )

    return None


def signal_is_past_event(
    signal,
):
    explicit_date = (
        extract_explicit_date(
            signal
        )
    )

    if not explicit_date:
        return False

    today = datetime.now(
        timezone.utc
    ).date()

    return (
        explicit_date
        < today
    )


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
        "username":
            "Hackathon Monitor",

        "embeds": [
            {
                "title": (
                    "🔎 POSSIBLE NEW "
                    "HACKATHON / EVENT"
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
                        "verify event date and "
                        "registration before "
                        "treating it as confirmed."
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
            "urls":
                successful_urls,

            "fingerprint":
                fingerprint(
                    signals
                ),

            "signals":
                signals,
        }

        previous = state.get(
            club_id
        )
        identities = signal_identities(club_id, signals)

        # Migrate existing snapshots without treating their current candidates
        # as new. Historical signals that already disappeared cannot be recovered.
        seen_ids = set(previous.get("seen_event_ids", [])) if previous else set()
        if previous and "seen_event_ids" not in previous:
            old_signals = previous.get("signals", [])
            old_identities = signal_identities(club_id, old_signals)
            seen_ids.update(
                old_identities[signal]
                for signal in old_signals
                if signal_is_high_value(club, signal)
            )

        if not previous:
            baseline += 1

            current["seen_event_ids"] = sorted(
                {identities[signal]
                for signal in signals
                if signal_is_high_value(club, signal)}
            )

            state[
                club_id
            ] = current

            print(
                f"BASELINE: "
                f"{len(signals)} "
                f"signals saved."
            )

            continue

        page_unchanged = (
            previous.get(
                "fingerprint"
            )
            == current[
                "fingerprint"
            ]
        )
        if page_unchanged:
            unchanged += 1
            print(
                f"UNCHANGED: "
                f"{len(signals)} "
                f"signals."
            )
        else:
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
            for signal in signals
            if signal_is_high_value(
                club,
                signal,
            )
        ]

        alertworthy = []
        pending_ids = set()

        for signal in high_value:
            event_id = identities[signal]
            if event_id in seen_ids or event_id in pending_ids:
                continue

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

                seen_ids.add(event_id)

                continue

            alertworthy.append(
                signal
            )
            pending_ids.add(event_id)

        if not page_unchanged or alertworthy:
            print(
                f"CHANGED: "
                f"{len(additions)} new signals, "
                f"{len(alertworthy)} "
                f"new event identities."
            )

            for signal in additions[:10]:
                print(f"  NEW: {signal[:300]}")

        # Each delivered batch contains only identities that will be marked sent.
        for offset in range(0, len(alertworthy), 6):
            batch = alertworthy[offset:offset + 6]
            try:
                if send_discord(
                    club,
                    batch,
                ):
                    alerts += 1
                    seen_ids.update(identities[signal] for signal in batch)
                    state[club_id] = {**current, "seen_event_ids": sorted(seen_ids)}
                    save_state(state)
                else:
                    break

            except Exception as exc:
                print(
                    "DISCORD FAILED: "
                    f"{type(exc).__name__}"
                )

                break

        current["seen_event_ids"] = sorted(seen_ids)
        state[
            club_id
        ] = current

    save_state(
        state
    )

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

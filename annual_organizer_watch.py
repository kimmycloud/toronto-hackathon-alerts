import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


SOURCES_FILE = Path("university_sources.json")
STATE_FILE = Path("organizer_state.json")

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


WATCH_TERMS = [
    "2027",
    "hackathon",
    "makeathon",
    "datathon",
    "designathon",
    "ctf",
    "capture the flag",
    "application",
    "applications",
    "apply",
    "registration",
    "register",
    "deadline",
    "schedule",
    "event",
    "participants",
    "hacker",
]


ALERT_PATTERNS = [
    r"\b2027\b",
    r"\bapplications?\b",
    r"\bapply\b",
    r"\bregistration\b",
    r"\bregister\b",
    r"\bdeadline\b",
    r"\bdates?\b",
    r"\bschedule\b",
    r"\bhackathon\b",
    r"\bmakeathon\b",
    r"\bdatathon\b",
    r"\bdesignathon\b",
    r"\bctf\b",
    r"\bcapture\s+the\s+flag\b",
]


def load_sources():
    with SOURCES_FILE.open(
        "r",
        encoding="utf-8",
    ) as f:
        data = json.load(f)

    return [
        source
        for source in data["sources"]
        if (
            source.get("active", True)
            and source.get("kind")
            == "annual_organizer"
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


def fetch(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30,
        allow_redirects=True,
    )

    if response.status_code == 403:
        return None, "BLOCKED"

    response.raise_for_status()

    return response, "OK"


def clean_text(value):
    return re.sub(
        r"\s+",
        " ",
        str(value or ""),
    ).strip()


def relevant_text(value):
    lower = value.lower()

    return any(
        term in lower
        for term in WATCH_TERMS
    )


def extract_signals(html, base_url):
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    signals = set()

    if soup.title:
        title = clean_text(
            soup.title.get_text(
                " ",
                strip=True,
            )
        )

        if relevant_text(title):
            signals.add(
                f"TITLE|{title}"
            )

    for tag in soup.find_all(
        [
            "h1",
            "h2",
            "h3",
            "h4",
        ]
    ):
        text = clean_text(
            tag.get_text(
                " ",
                strip=True,
            )
        )

        if (
            text
            and relevant_text(text)
        ):
            signals.add(
                f"HEADING|{text}"
            )

    for link in soup.find_all(
        "a",
        href=True,
    ):
        text = clean_text(
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
            f"{text} {url}"
        )

        if relevant_text(
            combined
        ):
            signals.add(
                f"LINK|{text}|{url}"
            )

    for tag in soup.find_all(
        [
            "p",
            "li",
            "span",
            "div",
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

        if relevant_text(text):
            signals.add(
                f"TEXT|{text}"
            )

    return sorted(signals)


def fingerprint(signals):
    joined = "\n".join(
        signals
    )

    return hashlib.sha256(
        joined.encode(
            "utf-8"
        )
    ).hexdigest()


def interesting_new_signals(
    old_signals,
    new_signals,
):
    old_set = set(
        old_signals or []
    )

    return [
        signal
        for signal in new_signals
        if signal not in old_set
    ]


def signal_is_alertworthy(
    signal,
):
    return any(
        re.search(
            pattern,
            signal,
            re.IGNORECASE,
        )
        for pattern
        in ALERT_PATTERNS
    )


def clean_signal_for_discord(
    signal,
):
    parts = signal.split(
        "|"
    )

    if not parts:
        return signal

    kind = parts[0]

    if (
        kind == "LINK"
        and len(parts) >= 3
    ):
        label = (
            parts[1]
            or "New link"
        )

        url = parts[2]

        return (
            f"• **{label}** — {url}"
        )

    if len(parts) >= 2:
        text = "|".join(
            parts[1:]
        )

        return (
            f"• {text}"
        )

    return (
        f"• {signal}"
    )


def send_discord_change(
    source,
    signals,
):
    if not DISCORD_WEBHOOK_URL:
        print(
            "DISCORD SKIPPED: "
            "DISCORD_WEBHOOK_URL "
            "not configured."
        )
        return False

    display_signals = [
        clean_signal_for_discord(
            signal
        )
        for signal in signals[:6]
    ]

    description = "\n".join(
        display_signals
    )

    if len(signals) > 6:
        description += (
            f"\n• +{len(signals) - 6} "
            f"more new signals"
        )

    payload = {
        "username": "Hackathon Monitor",
        "embeds": [
            {
                "title": (
                    "🔎 HACKATHON ORGANIZER "
                    "UPDATE DETECTED"
                ),
                "description": (
                    f"**{source['name']}** "
                    f"({source['school']}) "
                    f"changed its public "
                    f"event information.\n\n"
                    f"{description}"
                ),
                "url": source["url"],
                "footer": {
                    "text": (
                        "Discovery alert — "
                        "verify event date, "
                        "eligibility, and "
                        "registration before "
                        "treating this as a "
                        "confirmed hackathon."
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
        "DISCORD: organizer "
        "change alert sent."
    )

    return True


def main():
    sources = load_sources()
    state = load_state()

    print(
        f"Loaded {len(sources)} "
        f"annual organizer sources."
    )

    baseline = 0
    unchanged = 0
    changed = 0
    blocked = 0
    failed = 0
    alerts_sent = 0

    for source in sources:
        source_id = source["id"]

        print(
            f"\nChecking: "
            f"{source['school']} — "
            f"{source['name']}"
        )

        try:
            response, status = fetch(
                source["url"]
            )

            if status == "BLOCKED":
                blocked += 1

                print(
                    "BLOCKED: HTTP 403"
                )

                continue

            signals = extract_signals(
                response.text,
                response.url,
            )

            current_hash = fingerprint(
                signals
            )

            previous = state.get(
                source_id
            )

            new_state = {
                "url": response.url,
                "fingerprint": current_hash,
                "signals": signals,
            }

            if not previous:
                baseline += 1

                state[
                    source_id
                ] = new_state

                print(
                    f"BASELINE: "
                    f"{len(signals)} "
                    f"signals saved."
                )

                continue

            previous_hash = (
                previous.get(
                    "fingerprint"
                )
            )

            if (
                previous_hash
                == current_hash
            ):
                unchanged += 1

                state[
                    source_id
                ] = new_state

                print(
                    f"UNCHANGED: "
                    f"{len(signals)} "
                    f"signals."
                )

                continue

            changed += 1

            new_signals = (
                interesting_new_signals(
                    previous.get(
                        "signals",
                        [],
                    ),
                    signals,
                )
            )

            alertworthy = [
                signal
                for signal in new_signals
                if signal_is_alertworthy(
                    signal
                )
            ]

            print(
                f"CHANGED: "
                f"{len(new_signals)} "
                f"new signals, "
                f"{len(alertworthy)} "
                f"alertworthy."
            )

            for signal in new_signals[
                :20
            ]:
                print(
                    f"  NEW: "
                    f"{signal[:300]}"
                )

            if alertworthy:
                try:
                    sent = (
                        send_discord_change(
                            source,
                            alertworthy,
                        )
                    )

                    if sent:
                        alerts_sent += 1

                except Exception as exc:
                    failed += 1

                    print(
                        "DISCORD FAILED: "
                        f"{exc}"
                    )

                    # Do not advance this
                    # source's state if the
                    # notification failed.
                    continue

            state[
                source_id
            ] = new_state

        except Exception as exc:
            failed += 1

            print(
                f"FAILED: "
                f"{source_id}: "
                f"{exc}"
            )

    save_state(state)

    print()

    print(
        f"Finished: "
        f"{baseline} baseline, "
        f"{unchanged} unchanged, "
        f"{changed} changed, "
        f"{blocked} blocked, "
        f"{failed} failed, "
        f"{alerts_sent} Discord alerts."
    )


if __name__ == "__main__":
    main()

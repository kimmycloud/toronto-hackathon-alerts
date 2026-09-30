import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


SOURCES_FILE = Path("university_sources.json")
STATE_FILE = Path("organizer_state.json")

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

    # Page title
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

    # Important headings
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

    # Links are especially useful because
    # registration/application URLs often change.
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

        if relevant_text(combined):
            signals.add(
                f"LINK|{text}|{url}"
            )

    # Short text blocks containing strong signals.
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
    joined = "\n".join(signals)

    return hashlib.sha256(
        joined.encode("utf-8")
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

            state[source_id] = {
                "url": response.url,
                "fingerprint": current_hash,
                "signals": signals,
            }

            if not previous:
                baseline += 1

                print(
                    f"BASELINE: "
                    f"{len(signals)} signals saved."
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

                print(
                    f"UNCHANGED: "
                    f"{len(signals)} signals."
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

            print(
                f"CHANGED: "
                f"{len(new_signals)} "
                f"new signals."
            )

            for signal in new_signals[
                :20
            ]:
                print(
                    f"  NEW: "
                    f"{signal[:300]}"
                )

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
        f"{failed} failed."
    )


if __name__ == "__main__":
    main()

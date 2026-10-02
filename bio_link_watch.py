"""Watch public bio-link pages for newly added event and registration targets."""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from club_discovery_watch import (
    CORE_EVENT_PATTERNS,
    LOCAL_TECH_EVENT_PATTERNS,
    TORONTO_GTA_SCHOOLS,
    extract_explicit_date,
    matches_patterns,
)


SOURCES_FILE = Path("bio_link_sources.json")
STATE_FILE = Path("bio_link_state.json")
HEADERS = {"User-Agent": "TorontoHackathonAlerts/1.0 (public bio-link monitor)",
           "Accept": "text/html,application/xhtml+xml"}
TRACKING_KEYS = {"fbclid", "gclid", "dclid", "msclkid", "igshid", "mc_cid", "mc_eid", "ref_src"}
SOCIAL_HOSTS = {"instagram.com", "facebook.com", "linkedin.com", "tiktok.com",
                "x.com", "twitter.com", "youtube.com", "discord.gg", "discord.com"}
BIO_HOSTS = {"linktr.ee", "solo.to", "beacons.ai", "carrd.co", "bio.site"}
FORM_HOSTS = {"forms.gle"}
EVENT_HOSTS = {"luma.com", "lu.ma", "devpost.com", "eventbrite.com", "eventbrite.ca"}
REGISTRATION = re.compile(r"\b(?:register|registration|apply|application|sign[ -]?up|tickets?|rsvp)\b", re.I)


class SourceBlockedError(Exception):
    pass


class SourceUnavailableError(Exception):
    pass


def host_is(host, domain):
    return host == domain or host.endswith("." + domain)


def canonical_url(url):
    """Keep identifying query data, while removing fragments and known tracking data."""
    parts = urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return None
    host = parts.hostname.lower().rstrip(".")
    port = parts.port
    netloc = host if port is None or (parts.scheme.lower(), port) in {("http", 80), ("https", 443)} else f"{host}:{port}"
    query = sorted((key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                   if not key.lower().startswith("utm_") and key.lower() not in TRACKING_KEYS
                   and not (key.lower() == "usp" and (host_is(host, "docs.google.com") or host_is(host, "forms.gle"))))
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", urlencode(query), ""))


def load_sources():
    data = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    sources = [source for source in data["sources"] if source.get("active", True)]
    ids = [source["id"] for source in sources]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate bio-link source id")
    return sources


def load_state():
    if not STATE_FILE.exists():
        return {}
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise ValueError("Bio-link state must be an object")
    return state


def save_state(state):
    temporary = STATE_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(STATE_FILE)


def fetch(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
    except (requests.exceptions.SSLError, requests.exceptions.ConnectionError,
            requests.exceptions.Timeout) as exc:
        raise SourceUnavailableError(type(exc).__name__) from exc
    except requests.exceptions.RequestException as exc:
        raise SourceUnavailableError(type(exc).__name__) from exc
    if response.status_code in {403, 406, 429}:
        raise SourceBlockedError(f"HTTP {response.status_code}")
    if response.status_code == 404 or response.status_code >= 500:
        raise SourceUnavailableError(f"HTTP {response.status_code}")
    response.raise_for_status()
    return response.text, response.url


def clean_text(value):
    return re.sub(r"\s+", " ", value or "").strip()


def extract_links(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    source_host = urlsplit(base_url).hostname or ""
    found = {}
    for anchor in soup.find_all("a", href=True):
        target = canonical_url(urljoin(base_url, anchor["href"]))
        if not target:
            continue
        host = urlsplit(target).hostname or ""
        if host == source_host or any(host_is(host, item) for item in BIO_HOSTS | SOCIAL_HOSTS):
            continue
        label = clean_text(anchor.get_text(" ", strip=True))[:300]
        parent = anchor.parent
        context = (clean_text(parent.get_text(" ", strip=True))[:400]
                   if parent and parent.name not in {"body", "html"} and len(parent.find_all("a")) == 1
                   else "")
        found.setdefault(target, {"url": target, "label": label, "context": context})
        if not found[target]["label"] and label:
            found[target]["label"] = label
    return found


def relevant(source, link):
    host = urlsplit(link["url"]).hostname or ""
    text = f"{link['label']} {link['context']} {link['url']}"
    if matches_patterns(text, CORE_EVENT_PATTERNS):
        return True
    # Annual organizers often advertise the next edition on a year-specific
    # subdomain through a public bio-link before their homepage is updated.
    organizer_name = re.sub(r"[^a-z0-9]", "", source["organization"].lower())
    label_name = re.sub(r"[^a-z0-9]", "", link["label"].lower())
    if (organizer_name and organizer_name in label_name
            and re.search(r"\b20\d{2}\b", link["label"])
            and any(host_is(host, domain) for domain in source.get("organizer_domains", []))):
        return True
    if source["school"] not in TORONTO_GTA_SCHOOLS:
        return False
    if matches_patterns(text, LOCAL_TECH_EVENT_PATTERNS):
        return True
    form = host_is(host, "docs.google.com") and urlsplit(link["url"]).path.startswith("/forms/")
    eventbrite = host.startswith("eventbrite.") or ".eventbrite." in host
    platform = form or eventbrite or any(host_is(host, item) for item in FORM_HOSTS | EVENT_HOSTS)
    return platform or bool(REGISTRATION.search(f"{link['label']} {link['context']}"))


def past_explicit_date(link):
    explicit = extract_explicit_date(f"{link['label']} {link['context']} {link['url']}")
    return explicit is not None and explicit < datetime.now(timezone.utc).date()


def send_discord(source, link):
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        print("  DISCORD SKIPPED: DISCORD_WEBHOOK_URL not configured.")
        return False
    payload = {"username": "Hackathon Monitor", "embeds": [{
        "title": "🔎 NEW BIO-LINK EVENT LEAD",
        "description": (f"**{source['organization']}** ({source['school']})\n"
                        f"**New link:** {link['label'] or 'Unlabeled link'}\n"
                        f"**Destination:** {link['url']}\n"
                        f"**Bio-link source:** {source['url']}"),
        "footer": {"text": "Discovery lead — verify the event and date before treating it as confirmed."},
    }]}
    response = requests.post(webhook, json=payload, timeout=30)
    response.raise_for_status()
    print("  DISCORD: bio-link discovery lead sent.")
    return True


def main():
    sources = load_sources()
    state = load_state()
    print(f"Loaded {len(sources)} bio-link sources.")
    counts = {name: 0 for name in ("baseline", "unchanged", "changed", "blocked", "unavailable", "failed", "suppressed", "alerts")}
    for index, source in enumerate(sources, 1):
        print(f"[{index}/{len(sources)}] {source['school']} — {source['organization']}")
        try:
            html, final_url = fetch(source["url"])
            links = extract_links(html, final_url)
        except SourceBlockedError as exc:
            counts["blocked"] += 1
            print(f"BLOCKED: {exc}; {source['url']}")
            continue
        except SourceUnavailableError as exc:
            counts["unavailable"] += 1
            print(f"UNAVAILABLE: {exc}; {source['url']}")
            continue
        except Exception as exc:
            counts["failed"] += 1
            print(f"FAILED: {type(exc).__name__}; {source['url']}")
            continue

        previous = state.get(source["id"])
        if previous is None:
            state[source["id"]] = {"links": links}
            counts["baseline"] += 1
            print(f"BASELINE: {len(links)} targets stored.")
            continue
        old_links = previous["links"]
        added = {key: value for key, value in links.items() if key not in old_links}
        if not added:
            counts["unchanged"] += 1
            state[source["id"]] = {"links": links}
            print(f"UNCHANGED: {len(links)} targets.")
            continue
        counts["changed"] += 1
        pending = set()
        for key, link in added.items():
            if not relevant(source, link):
                continue
            if past_explicit_date(link):
                counts["suppressed"] += 1
                print(f"  SUPPRESSED PAST: {link['label'][:120]}")
                continue
            try:
                if send_discord(source, link):
                    counts["alerts"] += 1
                else:
                    pending.add(key)
            except Exception as exc:
                pending.add(key)
                print(f"  DISCORD FAILED: {type(exc).__name__}; delivery pending.")
        state[source["id"]] = {"links": {key: value for key, value in links.items() if key not in pending}}
        print(f"CHANGED: {len(added)} new targets, {len(pending)} pending alerts.")
    save_state(state)
    if counts["baseline"] and counts["baseline"] == len(sources):
        print(f"BIO-LINK BASELINE: {counts['baseline']} sources stored; no Discord alerts.")
    print("Finished: " + ", ".join(f"{value} {key}" for key, value in counts.items()) + ".")


if __name__ == "__main__":
    main()

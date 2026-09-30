"""Report changes in source health to the private Discord webhook."""

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

STATE_FILE = Path("monitor_health_state.json")
BAD_STATUSES = {"BLOCKED", "UNAVAILABLE", "FAILED"}
HEALTH_STATUSES = BAD_STATUSES | {"UNCHANGED", "CHANGED", "BASELINE", "SKIPPED"}


def load_state():
    if not STATE_FILE.exists():
        return {}
    with STATE_FILE.open(encoding="utf-8") as stream:
        state = json.load(stream)
    if not isinstance(state, dict):
        raise ValueError("Health state must be a JSON object")
    return state


def save_state(state):
    temporary = STATE_FILE.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(state, stream, indent=2, ensure_ascii=False, sort_keys=True)
        stream.write("\n")
    temporary.replace(STATE_FILE)


def send_discord(description):
    webhook = os.environ.get("MONITOR_ERROR_WEBHOOK_URL")
    if not webhook:
        raise RuntimeError("MONITOR_ERROR_WEBHOOK_URL is not configured")
    payload = {
        "username": "Monitor Health",
        "embeds": [{"title": "⚠️ Monitor Health Changed", "description": description}],
    }
    request = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"Discord returned HTTP {response.status}")
    print("ERROR DISCORD: health alert sent.")


def problem_key(monitor, name):
    return hashlib.sha256(f"{monitor}|{name}".encode("utf-8")).hexdigest()


def clean_error(value):
    # Keep volatile transport details out of the state and Discord message.
    return re.sub(r"\s+", " ", str(value or "")).strip()[:180]


def compare_health(monitor, problems, skipped=()):
    state = load_state()
    previous = state.get(monitor)
    if previous is not None and not isinstance(previous, dict):
        raise ValueError(f"Invalid health state for {monitor}")
    current = {}
    for problem in problems:
        if problem["status"] not in BAD_STATUSES:
            continue
        name = problem["name"]
        current[problem_key(monitor, name)] = {
            "name": name,
            "status": problem["status"],
            "error": clean_error(problem.get("error")),
        }

    # A skipped source gives no evidence of recovery.
    for name in skipped:
        key = problem_key(monitor, name)
        if previous and key in previous:
            current[key] = previous[key]

    if previous is None:
        state[monitor] = current
        save_state(state)
        print(f"HEALTH BASELINE: {len(current)} known problem(s) stored; no Discord alert.")
        return

    changed = [item for key, item in current.items()
               if key not in previous or previous[key].get("status") != item["status"]]
    recovered = [item for key, item in previous.items() if key not in current]
    if not changed and not recovered:
        print(f"HEALTH UNCHANGED: {len(current)} known problem(s).")
        return

    lines = []
    for item in changed:
        line = f"• **{item['status']}** — {item['name']}"
        if item["error"]:
            line += f"\n  {item['error']}"
        lines.append(line)
    for item in recovered:
        lines.append(f"• ✅ **RECOVERED** — {item['name']}")

    # One Discord embed has a 4096-character description limit.
    message = f"{monitor}: {len(lines)} change(s)\n"
    shown = 0
    for line in lines:
        candidate = message + line + "\n"
        if len(candidate) > 3800:
            break
        message = candidate
        shown += 1
    if shown < len(lines):
        message += f"• +{len(lines) - shown} more change(s)\n"

    # Preserve the previous state until delivery succeeds, so the next run retries.
    send_discord(message)
    state[monitor] = current
    save_state(state)
    print(f"HEALTH CHANGED: {len(lines)} change(s).")


def university_discovery_health(path):
    with Path(path).open(encoding="utf-8") as stream:
        data = json.load(stream)
    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("Discovery JSON has no source statuses")
    problems = []
    names = set()
    for source in sources:
        name, status = source.get("source"), source.get("status")
        if not name or not status or name in names:
            raise ValueError("Invalid or duplicate discovery source status")
        names.add(name)
        if status in BAD_STATUSES:
            problems.append({"name": name, "status": status, "error": source.get("error")})
        elif status != "SUCCEEDED":
            raise ValueError(f"Unknown discovery status: {status}")
    compare_health("university-discovery", problems)


def log_health(path, monitor, heading_pattern):
    text = Path(path).read_text(encoding="utf-8")
    lines = text.splitlines()
    match = re.search(r"^Loaded (\d+) (?:club discovery entries|annual organizer sources)\.$", text, re.M)
    if not match or not re.search(r"^Finished:", text, re.M):
        raise ValueError("Incomplete monitor log")
    expected = int(match.group(1))
    current_name = None
    seen = set()
    statuses = {}
    for raw in lines:
        heading = heading_pattern.match(raw)
        if heading:
            current_name = f"{heading.group(1)} — {heading.group(2)}"
            if current_name in seen:
                raise ValueError("Duplicate monitor heading")
            seen.add(current_name)
            continue
        if current_name is None or raw.startswith("  "):
            continue
        status_line = re.match(r"^(BLOCKED|UNAVAILABLE|FAILED|UNCHANGED|CHANGED|BASELINE|SKIPPED):\s*(.*)$", raw)
        if status_line:
            status, error = status_line.groups()
            statuses[current_name] = {"name": current_name, "status": status, "error": error}
    if len(seen) != expected or set(statuses) != seen:
        raise ValueError("Incomplete monitor source statuses")
    problems = [item for item in statuses.values() if item["status"] in BAD_STATUSES]
    skipped = [item["name"] for item in statuses.values() if item["status"] == "SKIPPED"]
    compare_health(monitor, problems, skipped)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--type", required=True, choices=[
        "university-discovery", "club-discovery", "annual-organizer"])
    parser.add_argument("--file", required=True)
    args = parser.parse_args()
    try:
        if args.type == "university-discovery":
            university_discovery_health(args.file)
        else:
            heading = (r"^\[\d+/\d+\] (.+?) — (.+)$" if args.type == "club-discovery"
                       else r"^Checking: (.+?) — (.+)$")
            log_health(args.file, args.type, re.compile(heading))
    except Exception as exc:
        # Never print an exception that could include the webhook URL.
        print(f"HEALTH REPORT FAILED: {type(exc).__name__}; state not advanced.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

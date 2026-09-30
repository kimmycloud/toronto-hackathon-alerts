import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path


ERROR_WEBHOOK_URL = os.environ.get(
    "MONITOR_ERROR_WEBHOOK_URL"
)

STATE_FILE = Path(
    "monitor_health_state.json"
)

BAD_STATUSES = {
    "FAILED",
    "BLOCKED",
    "UNAVAILABLE",
}


# --------------------------------------------------
# State
# --------------------------------------------------

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


# --------------------------------------------------
# Discord
# --------------------------------------------------

def send_discord(
    title,
    description,
):
    if not ERROR_WEBHOOK_URL:
        print(
            "ERROR DISCORD SKIPPED: "
            "MONITOR_ERROR_WEBHOOK_URL "
            "not configured."
        )

        return False

    payload = {
        "username": "Monitor Health",
        "embeds": [
            {
                "title": title,
                "description": description,
            }
        ],
    }

    body = json.dumps(
        payload
    ).encode(
        "utf-8"
    )

    request = urllib.request.Request(
        ERROR_WEBHOOK_URL,
        data=body,
        headers={
            "Content-Type":
                "application/json"
        },
        method="POST",
    )

    with urllib.request.urlopen(
        request,
        timeout=30,
    ) as response:
        if not (
            200
            <= response.status
            < 300
        ):
            raise RuntimeError(
                "Discord returned "
                f"HTTP {response.status}"
            )

    print(
        "ERROR DISCORD: "
        "health alert sent."
    )

    return True


# --------------------------------------------------
# Problem normalization
# --------------------------------------------------

def problem_key(
    monitor,
    name,
):
    raw = (
        f"{monitor}|{name}"
    )

    return hashlib.sha256(
        raw.encode(
            "utf-8"
        )
    ).hexdigest()


def compare_health(
    monitor,
    current_problems,
):
    state = load_state()

    monitor_state = state.get(
        monitor
    )

    current = {}

    for problem in current_problems:
        key = problem_key(
            monitor,
            problem["name"],
        )

        current[key] = problem

    # First run for this monitor:
    # baseline existing problems silently.
    if monitor_state is None:
        state[monitor] = current

        save_state(
            state
        )

        print(
            f"HEALTH BASELINE: "
            f"{len(current)} known "
            f"problem(s) stored; "
            f"no Discord alert."
        )

        return

    previous = monitor_state

    new_or_changed = []

    recovered = []

    for key, problem in current.items():
        old = previous.get(
            key
        )

        if old is None:
            new_or_changed.append(
                problem
            )

            continue

        if (
            old.get("status")
            != problem.get("status")
            or old.get("error")
            != problem.get("error")
        ):
            new_or_changed.append(
                problem
            )

    for key, old in previous.items():
        if key not in current:
            recovered.append(
                old
            )

    if (
        not new_or_changed
        and not recovered
    ):
        print(
            "HEALTH UNCHANGED: "
            f"{len(current)} known "
            f"problem(s)."
        )

        state[
            monitor
        ] = current

        save_state(
            state
        )

        return

    lines = []

    for problem in new_or_changed[
        :12
    ]:
        line = (
            f"• **{problem['status']}** — "
            f"{problem['name']}"
        )

        error = problem.get(
            "error",
            ""
        )

        if error:
            line += (
                f"\n  {error[:250]}"
            )

        lines.append(
            line
        )

    for problem in recovered[
        :12
    ]:
        lines.append(
            f"• ✅ **RECOVERED** — "
            f"{problem['name']}"
        )

    extra = (
        len(new_or_changed)
        + len(recovered)
        - len(lines)
    )

    if extra > 0:
        lines.append(
            f"• +{extra} more change(s)"
        )

    description = "\n".join(
        lines
    )

    send_discord(
        "⚠️ Monitor Health Changed",
        description,
    )

    state[
        monitor
    ] = current

    save_state(
        state
    )


# --------------------------------------------------
# University discovery JSON
# --------------------------------------------------

def university_discovery_health(
    path,
):
    with Path(path).open(
        "r",
        encoding="utf-8",
    ) as f:
        data = json.load(f)

    problems = []

    for source in data.get(
        "sources",
        [],
    ):
        status = source.get(
            "status",
            ""
        )

        if status not in BAD_STATUSES:
            continue

        problems.append(
            {
                "name":
                    source.get(
                        "source",
                        "unknown",
                    ),

                "status":
                    status,

                "error":
                    source.get(
                        "error",
                        "",
                    ),
            }
        )

    compare_health(
        "university-discovery",
        problems,
    )

    return 0


# --------------------------------------------------
# Club discovery log
# --------------------------------------------------

def club_discovery_health(
    path,
):
    text = Path(path).read_text(
        encoding="utf-8"
    )

    current_name = None

    problems = []

    heading_pattern = re.compile(
        r"^\[\d+/\d+\]\s+(.+?)\s+—\s+(.+)$"
    )

    for raw_line in text.splitlines():
        line = raw_line.strip()

        heading = heading_pattern.match(
            line
        )

        if heading:
            school = heading.group(
                1
            )

            club = heading.group(
                2
            )

            current_name = (
                f"{school} — {club}"
            )

            continue

        if current_name is None:
            continue

        for status in [
            "BLOCKED",
            "UNAVAILABLE",
            "FAILED",
        ]:
            prefix = (
                status + ":"
            )

            if line.startswith(
                prefix
            ):
                error = line[
                    len(prefix):
                ].strip()

                problems.append(
                    {
                        "name":
                            current_name,

                        "status":
                            status,

                        "error":
                            error,
                    }
                )

                break

    compare_health(
        "club-discovery",
        problems,
    )

    return 0


# --------------------------------------------------
# Main
# --------------------------------------------------

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--type",
        required=True,
        choices=[
            "university-discovery",
            "club-discovery",
        ],
    )

    parser.add_argument(
        "--file",
        required=True,
    )

    args = parser.parse_args()

    if (
        args.type
        == "university-discovery"
    ):
        return university_discovery_health(
            args.file
        )

    if (
        args.type
        == "club-discovery"
    ):
        return club_discovery_health(
            args.file
        )

    return 0


if __name__ == "__main__":
    sys.exit(
        main()
    )

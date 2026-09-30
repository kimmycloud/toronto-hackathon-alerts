import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path


ERROR_WEBHOOK_URL = os.environ.get(
    "MONITOR_ERROR_WEBHOOK_URL"
)


BAD_STATUSES = {
    "FAILED",
    "BLOCKED",
    "UNAVAILABLE",
}


def send_discord(title, description):
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


def university_discovery_health(
    path,
):
    with Path(path).open(
        "r",
        encoding="utf-8",
    ) as file:
        data = json.load(file)

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

        name = source.get(
            "source",
            "unknown"
        )

        error = source.get(
            "error",
            ""
        )

        problems.append(
            (
                status,
                name,
                error,
            )
        )

    if not problems:
        print(
            "HEALTHY: no blocked, "
            "unavailable, or failed "
            "university discovery sources."
        )

        return 0

    lines = []

    for status, name, error in problems:
        line = (
            f"• **{status}** — "
            f"`{name}`"
        )

        if error:
            line += (
                f"\n  {error[:300]}"
            )

        lines.append(
            line
        )

    description = (
        f"University discovery has "
        f"**{len(problems)} source "
        f"problem(s)**.\n\n"
        + "\n".join(
            lines[:15]
        )
    )

    if len(problems) > 15:
        description += (
            f"\n\n+{len(problems) - 15} "
            f"more"
        )

    send_discord(
        "⚠️ University Discovery Health",
        description,
    )

    return 0


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--type",
        required=True,
        choices=[
            "university-discovery",
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

    return 0


if __name__ == "__main__":
    sys.exit(
        main()
    )

import json
import os
import requests

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dateparser.search import search_dates
from dateutil.relativedelta import relativedelta

from hackathon_monitor import (
    find_devpost_candidates,
    find_mlh_candidates,
    get_devpost_location,
    find_target_location,
    find_registration_links,
    registration_closed,
    extract_deadline,
    get_year_from_text,
    event_key,
    load_sent,
)


REMINDER_FILE = "reminder_history.json"


def load_reminders():
    try:
        with open(REMINDER_FILE, "r") as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()


def save_reminders(reminders):
    with open(REMINDER_FILE, "w") as f:
        json.dump(sorted(reminders), f, indent=2)


def parse_devpost_start_date(text):
    matches = search_dates(
        text or "",
        settings={"PREFER_DATES_FROM": "future"},
    )

    if not matches:
        return None

    return matches[0][1].date()


def send_reminder(
    label,
    name,
    location,
    event_dates,
    event_url,
):
    registration_links = find_registration_links(event_url)

    registration_url = (
        registration_links[0]
        if registration_links
        else None
    )

    # Don't remind about something you can no longer register for
    if registration_closed(event_url):
        return False

    if registration_url and registration_closed(registration_url):
        return False

    deadline = extract_deadline(event_url)

    if not deadline and registration_url:
        deadline = extract_deadline(registration_url)

    today = datetime.now(
        ZoneInfo("America/Toronto")
    ).date()

    if deadline and deadline["date"].date() < today:
        return False

    if deadline:
        deadline_text = deadline["date"].strftime("%B %d, %Y")
    else:
        deadline_text = "Not found — verify registration is still open"

    message = (
        f"🔔 **REMINDER: HACKATHON {label} AWAY**\n\n"
        f"## {name}\n"
        f"📍 **Location:** {location}\n"
        f"🗓️ **Hackathon:** {event_dates}\n"
        f"⏰ **Registration deadline:** {deadline_text}\n\n"
        f"🔗 **Hackathon:** {event_url}\n"
    )

    if registration_url:
        message += f"📝 **Register:** {registration_url}\n"

    response = requests.post(
        os.environ["DISCORD_WEBHOOK_URL"],
        json={"content": message},
        timeout=30,
    )

    response.raise_for_status()

    return True


def check_event(
    name,
    location,
    event_dates,
    event_url,
    start_date,
    year,
    sent,
    reminders,
):
    key = event_key(name, year)

    # Only remind about events that received an original alert
    if key not in sent and event_url not in sent:
        return

    today = datetime.now(
        ZoneInfo("America/Toronto")
    ).date()

    two_month_date = start_date - relativedelta(months=2)
    one_month_date = start_date - relativedelta(months=1)

    reminder_options = [
        (
            "2 MONTHS",
            two_month_date,
            f"{key}::2-month",
        ),
        (
            "1 MONTH",
            one_month_date,
            f"{key}::1-month",
        ),
    ]

    for label, target_date, reminder_id in reminder_options:

        if reminder_id in reminders:
            continue

        # 2-day grace period in case GitHub Actions misses a run
        if target_date <= today <= target_date + timedelta(days=2):

            if send_reminder(
                label,
                name,
                location,
                event_dates,
                event_url,
            ):
                reminders.add(reminder_id)
                save_reminders(reminders)

                print(
                    f"✅ Sent {label} reminder: {name}"
                )


def main():
    print("Checking hackathon reminders...")

    sent = load_sent()
    reminders = load_reminders()

    today = datetime.now(
        ZoneInfo("America/Toronto")
    ).date()

    # DEVPOST
    for hackathon in find_devpost_candidates():

        location, _ = find_target_location(
            get_devpost_location(hackathon)
        )

        if not location:
            continue

        if hackathon.get("open_state") != "upcoming":
            continue

        name = hackathon.get("title", "Unknown")
        url = hackathon.get("url")

        event_dates = hackathon.get(
            "submission_period_dates",
            "Date unavailable",
        )

        start_date = parse_devpost_start_date(event_dates)

        if not start_date or start_date <= today:
            continue

        year = get_year_from_text(event_dates)

        check_event(
            name,
            location,
            event_dates,
            url,
            start_date,
            year,
            sent,
            reminders,
        )

    # MLH
    for event in find_mlh_candidates():

        start_date = event["start_date"].date()

        if start_date <= today:
            continue

        check_event(
            event["name"],
            event["location"],
            event["event_dates"],
            event["url"],
            start_date,
            event["year"],
            sent,
            reminders,
        )

    print("Reminder check complete.")


if __name__ == "__main__":
    main()

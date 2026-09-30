import os

from annual_organizer_watch import send_discord_change


def main():
    if not os.environ.get("DISCORD_WEBHOOK_URL"):
        raise RuntimeError(
            "DISCORD_WEBHOOK_URL is not configured"
        )

    fake_source = {
        "name": "Test Hackathon Organizer",
        "school": "TEST ONLY",
        "url": "https://example.com/",
    }

    fake_signals = [
        (
            "LINK|Applications Open — TEST ONLY|"
            "https://example.com/apply"
        ),
        (
            "TEXT|TEST ONLY: Applications for the "
            "2027 hackathon are now open."
        ),
    ]

    sent = send_discord_change(
        fake_source,
        fake_signals,
    )

    if not sent:
        raise RuntimeError(
            "Discord test alert was not sent"
        )

    print(
        "SUCCEEDED: fake organizer change "
        "alert sent to Discord."
    )


if __name__ == "__main__":
    main()

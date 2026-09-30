from datetime import date

from club_discovery_watch import extract_explicit_date


TESTS = {
    "16. Sept. 2026": date(2026, 9, 16),
    "September 16, 2026": date(2026, 9, 16),
    "2026-09-16": date(2026, 9, 16),
    "16/09/2026": date(2026, 9, 16),
    "16.09.2026": date(2026, 9, 16),
    "2026년 9월 16일": date(2026, 9, 16),
    "2026年9月16日": date(2026, 9, 16),
}


for text, expected in TESTS.items():
    actual = extract_explicit_date(text)

    status = (
        "SUCCEEDED"
        if actual == expected
        else "FAILED"
    )

    print(
        f"{status}: "
        f"{text!r} -> {actual}"
    )

    if actual != expected:
        raise SystemExit(1)


print(
    "SUCCEEDED: all date formats parsed correctly."
)

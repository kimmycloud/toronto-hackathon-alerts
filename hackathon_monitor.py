TARGET_LOCATIONS = [
    # Toronto
    "Toronto",
    "North York",
    "Scarborough",
    "Etobicoke",

    # GTA
    "Markham",
    "Mississauga",
    "Brampton",
    "Vaughan",
    "Richmond Hill",
    "Oakville",
    "Burlington",
    "Milton",
    "Pickering",
    "Ajax",
    "Whitby",
    "Oshawa",

    # Nearby
    "Waterloo",
    "Kitchener",
    "Cambridge",
    "Hamilton",
]


def is_target_location(text):
    text = text.lower()

    return any(
        location.lower() in text
        for location in TARGET_LOCATIONS
    )


if __name__ == "__main__":
    print("Hackathon monitor started.")
    print(f"Watching {len(TARGET_LOCATIONS)} locations.")

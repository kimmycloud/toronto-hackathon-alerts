import requests

TARGET_LOCATIONS = [
    "Toronto", "North York", "Scarborough", "Etobicoke",
    "Markham", "Mississauga", "Brampton", "Vaughan",
    "Richmond Hill", "Oakville", "Burlington", "Milton",
    "Pickering", "Ajax", "Whitby", "Oshawa",
    "Waterloo", "Kitchener", "Cambridge", "Hamilton",
]

DEVPOST_API = "https://devpost.com/api/hackathons"


def find_devpost_candidates():
    response = requests.get(DEVPOST_API, timeout=30)
    response.raise_for_status()

    data = response.json()
    return data["hackathons"]


def location_matches(hackathon):
    location_data = hackathon.get("displayed_location") or {}
    location = location_data.get("location", "")

    for target in TARGET_LOCATIONS:
        if target.lower() in location.lower():
            return target

    return None


if __name__ == "__main__":
    print("Hackathon monitor started.")

    hackathons = find_devpost_candidates()

    print(f"Found {len(hackathons)} Devpost hackathons.\n")
    print("Matching our locations:\n")

    matches = 0

    for hackathon in hackathons:
        location = location_matches(hackathon)

        if location:
            matches += 1

            print(f"✅ {hackathon['title']}")
            print(f"Location: {hackathon['displayed_location']['location']}")
            print(f"Status: {hackathon['open_state']}")
            print(f"Dates: {hackathon['submission_period_dates']}")
            print(hackathon["url"])
            print()

    print(f"Total location matches: {matches}")

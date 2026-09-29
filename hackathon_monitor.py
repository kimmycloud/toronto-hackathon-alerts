import requests

TARGET_LOCATIONS = [
    "Toronto", "North York", "Scarborough", "Etobicoke",
    "Markham", "Mississauga", "Brampton", "Vaughan",
    "Richmond Hill", "Oakville", "Burlington", "Milton",
    "Pickering", "Ajax", "Whitby", "Oshawa",
    "Waterloo", "Kitchener", "Cambridge", "Hamilton",
]

DEVPOST_API = "https://devpost.com/api/hackathons"


def find_devpost_candidates(max_pages=20):
    all_hackathons = []
    seen_ids = set()

    for page in range(1, max_pages + 1):
        response = requests.get(
            DEVPOST_API,
            params={"page": page},
            timeout=30
        )
        response.raise_for_status()

        data = response.json()
        hackathons = data.get("hackathons", [])

        if not hackathons:
            break

        new_items = 0

        for hackathon in hackathons:
            hackathon_id = hackathon.get("id") or hackathon.get("url")

            if hackathon_id not in seen_ids:
                seen_ids.add(hackathon_id)
                all_hackathons.append(hackathon)
                new_items += 1

        # Prevent looping if Devpost repeats the same page
        if new_items == 0:
            break

    return all_hackathons


def get_location(hackathon):
    location_data = hackathon.get("displayed_location")

    if isinstance(location_data, dict):
        return location_data.get("location", "")

    if isinstance(location_data, str):
        return location_data

    return ""


def location_matches(hackathon):
    location = get_location(hackathon)

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

            print(f"✅ {hackathon.get('title')}")
            print(f"Location: {get_location(hackathon)}")
            print(f"Status: {hackathon.get('open_state')}")
            print(f"Dates: {hackathon.get('submission_period_dates')}")
            print(hackathon.get("url"))
            print()

    print(f"Total location matches: {matches}")

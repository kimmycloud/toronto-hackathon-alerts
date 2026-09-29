import requests
from bs4 import BeautifulSoup

MLH_URL = "https://www.mlh.com/seasons/2027/events"

TARGET_LOCATIONS = [
    "Toronto",
    "Scarborough",
    "Markham",
    "Mississauga",
    "Missisauga",  # MLH currently has this misspelling
    "Hamilton",
    "Waterloo",
    "Kitchener",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 HackathonAlertBot/1.0"
}


def main():
    response = requests.get(
        MLH_URL,
        headers=HEADERS,
        timeout=30
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")

    print("MLH page downloaded successfully.\n")

    page_text = soup.get_text(" ", strip=True)

    for location in TARGET_LOCATIONS:
        if location.lower() in page_text.lower():
            print(f"✅ Found location: {location}")


if __name__ == "__main__":
    main()

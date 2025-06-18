import urllib.request
from bs4 import BeautifulSoup
import re
import json
import jwt
import time
import json
import os
from dataclasses import dataclass

# Environment variables:
# REPO_NAME: Repository name in format "user/repo"
# ISSUE_TITLE_FORMAT: Issue title format, {} is replaced with the macOS version
# ISSUE_LABELS: Comma-separated list of issue labels (optional)
# APP_ID: The GitHub app ID
# INSTALLATION_ID: The GitHub app installation ID
# APP_PRIVATE_KEY: The GitHub app private key in PEM format


def get_env(name, mandatory=True):
    value = os.environ.get(name)
    if mandatory and (value is None or len(value) == 0):
        raise RuntimeError(f"Missing environment variable {name}")
    return value


REPO_NAME = get_env("REPO_NAME")
ISSUE_TITLE_FORMAT = get_env("ISSUE_TITLE_FORMAT")
ISSUE_LABELS = get_env("ISSUE_LABELS", False)
APP_ID = get_env("APP_ID")
INSTALLATION_ID = get_env("INSTALLATION_ID")
PRIVATE_KEY_PEM_ENV = get_env("APP_PRIVATE_KEY")

APP_NAME = "macos-release-tracker"
VERSION_HISTORY_SOURCE = "https://en.wikipedia.org/wiki/MacOS_version_history"
ISSUE_VERSION_FORMAT = "macOS {version} {build}"
ISSUE_VERSION_REGEX = r"^.*macOS\s+(\d+)\.(\d+)(?:\.(\d+))?\s+([A-Za-z0-9]{2,}).*$"

PRIVATE_KEY_PATH = None  # Set for testing
if PRIVATE_KEY_PATH is not None:
    with open(PRIVATE_KEY_PATH, "r") as f:
        PRIVATE_KEY_PEM = f.read()
elif PRIVATE_KEY_PEM_ENV is not None:
    PRIVATE_KEY_PEM = PRIVATE_KEY_PEM_ENV
else:
    raise RuntimeError("Missing GitHub app private key")

ISSUE_LABELS = ISSUE_LABELS.split(',') if ISSUE_LABELS else []


@dataclass
class MacVersion:
    version_major: int
    version_minor: int
    version_patch: int = 0
    build_number: str = ""
    release_date: str = ""

    def full_version(self):
        return f"{self.version_major}.{self.version_minor}.{self.version_patch}"

    def __str__(self):
        return f"{self.version_major}.{self.version_minor}.{self.version_patch} {self.build_number}"

    def is_newer_than(self, other: "MacVersion") -> bool:
        if self.version_major > other.version_major:
            return True
        if self.version_major < other.version_major:
            return False

        if self.version_minor > other.version_minor:
            return True
        if self.version_minor < other.version_minor:
            return False

        if self.version_patch > other.version_patch:
            return True
        if self.version_patch < other.version_patch:
            return False

        return self.build_number != other.build_number


def fetch_latest_macos_version() -> MacVersion:
    url = VERSION_HISTORY_SOURCE
    headers = {"User-Agent": "Mozilla/5.0"}

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as response:
        html = response.read()

    soup = BeautifulSoup(html, "html.parser")

    # Find the div with a heading containing "Releases"
    releases_div = None
    for div in soup.find_all("div"):
        heading = div.find(re.compile("^h[1-6]$"))
        if heading and "Releases" in heading.get_text():
            releases_div = div
            break

    if not releases_div:
        raise ValueError("Could not find the div with a Releases heading")

    # Find the first table that follows this div
    releases_table = None
    for sibling in releases_div.find_all_next():
        if sibling.name == "table" and "wikitable" in sibling.get("class", []):
            releases_table = sibling
            break

    if not releases_table:
        raise ValueError("Could not find the Releases table")

    # Get the last row (latest beta or release)
    rows = [tr for tr in releases_table.find_all("tr") if len(tr.find_all("td")) > 1]
    last_row = rows[-1]
    cells = last_row.find_all("td")

    if len(cells) < 3:
        raise ValueError("Unexpected table format")

    version_text = cells[-1].get_text().strip()  # Last column
    version_match = re.search(r"(\d+\.\d+(?:\.\d+)?)", version_text)
    build_match = re.search(r"\(([A-Za-z0-9]+)\)", version_text)
    release_date = re.search(r"\(([^\(]+[0-9]{4})\)", version_text)

    if not version_match or not build_match:
        raise ValueError("Failed to parse version or build number")

    version = version_match.group(1)
    build = build_match.group(1)
    date = release_date.group(1)

    parts = version.split(".")
    return MacVersion(
        version_major=int(parts[0]),
        version_minor=int(parts[1]),
        version_patch=int(parts[2]) if len(parts) > 2 else 0,
        build_number=build,
        release_date=date,
    )


def generate_jwt():
    now = int(time.time())
    payload = {"iat": now - 60, "exp": now + (10 * 60), "iss": APP_ID}
    encoded_jwt = jwt.encode(payload, PRIVATE_KEY_PEM, algorithm="RS256")
    return encoded_jwt


def get_installation_access_token():
    jwt_token = generate_jwt()

    url = f"https://api.github.com/app/installations/{INSTALLATION_ID}/access_tokens"
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "python-urllib",
    }

    req = urllib.request.Request(url, headers=headers, method="POST")
    with urllib.request.urlopen(req) as response:
        response_data = json.load(response)
    return response_data["token"]


def search_issues(title_regex_pattern, token):
    issues = []
    page = 1
    headers = {
        "Authorization": f"token {token}",
        "User-Agent": "python-urllib",
        "Accept": "application/vnd.github+json",
    }

    pattern = re.compile(title_regex_pattern, re.IGNORECASE)

    while True:
        params = urllib.parse.urlencode(
            {
                "state": "all",
                "per_page": "100",
                "page": str(page),
            }
        )
        url = f"https://api.github.com/repos/{REPO_NAME}/issues?{params}"

        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as response:
            data = json.load(response)

        if not data:
            break

        for issue in data:
            if not APP_NAME in issue["user"]["login"] or not issue["user"][
                "login"
            ].endswith("[bot]"):
                continue
            match = pattern.match(issue["title"])
            if not match:
                continue
            issues.append(
                MacVersion(
                    version_major=int(match.group(1)),
                    version_minor=int(match.group(2)),
                    version_patch=(
                        int(match.group(3)) if match.group(3) is not None else 0
                    ),
                    build_number=match.group(4),
                )
            )

        page += 1

    return issues


def create_issue(title, body, token):
    url = f"https://api.github.com/repos/{REPO_NAME}/issues"
    headers = {
        "Authorization": f"token {token}",
        "User-Agent": "python-urllib",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    }
    payload = json.dumps(
        {
            "title": title,
            "body": body,
            "labels": ISSUE_LABELS,
        }
    ).encode("utf-8")

    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req) as response:
        response_data = json.load(response)

    return response_data


def issue_details_for_mac_release(version: MacVersion):
    full_version = version.full_version()
    title = ISSUE_TITLE_FORMAT.format(
        ISSUE_VERSION_FORMAT.format(version=full_version, build=version.build_number)
    )
    body = f"""
New macOS version available:

|||  
|:-|:-|
|Version|{full_version}|
|Build|{version.build_number}|
|Date|{version.release_date}|

Source: {VERSION_HISTORY_SOURCE}
    """
    return (title, body)


def main():
    latest_version = fetch_latest_macos_version()
    print(f"Latest macOS version: {latest_version}")

    token = get_installation_access_token()
    posted_versions = search_issues(ISSUE_VERSION_REGEX, token)
    need_issue = all([latest_version.is_newer_than(other) for other in posted_versions])

    if need_issue:
        print(f"No existing issue found for this version")
        title, body = issue_details_for_mac_release(latest_version)
        print(f"Creating new issue with title: {title}")
        create_issue(title, body, token)
    else:
        print(f"Issue already exists, nothing to do")


if __name__ == "__main__":
    main()

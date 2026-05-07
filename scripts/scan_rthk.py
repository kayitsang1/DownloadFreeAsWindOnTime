import argparse
import csv
import os
import re
from datetime import datetime
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup


KEYWORD = "馬鼎盛"

SEARCH_QUERIES = [
    "講東講西 馬鼎盛",
    "講東講西 週日版 馬鼎盛",
    "Free_as_the_wind 馬鼎盛",
    "free_as_the_wind_sunday 馬鼎盛",
]

ALLOWED_PATHS = [
    "/radio/radio1/programme/Free_as_the_wind/episode/",
    "/radio/radio1/programme/free_as_the_wind_sunday/episode/",
]


def fetch(url):
    headers = {
        "User-Agent": "Mozilla/5.0 RTHK scanner"
    }
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.text


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def parse_date(text):
    patterns = [
        r"(\d{2})/(\d{2})/(\d{4})",
        r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})",
    ]

    for p in patterns:
        m = re.search(p, text)
        if not m:
            continue

        if len(m.group(1)) == 4:
            y, mo, d = map(int, m.groups())
        else:
            d, mo, y = map(int, m.groups())

        return datetime(y, mo, d).date()

    return None


def is_allowed_episode(url):
    return any(path in url for path in ALLOWED_PATHS)


def search_episode_urls():
    urls = set()

    for query in SEARCH_QUERIES:
        encoded = quote(query)
        search_url = f"https://websearch.rthk.hk/search?lang=tc&query={encoded}"

        html = fetch(search_url)
        soup = BeautifulSoup(html, "lxml")

        for a in soup.find_all("a", href=True):
            href = urljoin(search_url, a["href"])
            if is_allowed_episode(href):
                urls.add(href.split("?")[0])

    return sorted(urls)


def extract_detail(episode_url):
    html = fetch(episode_url)
    soup = BeautifulSoup(html, "lxml")

    text = clean(soup.get_text("\n"))

    title = ""
    for tag in soup.find_all(["h1", "h2", "h3"]):
        t = clean(tag.get_text())
        if t and "講東講西" not in t:
            title = t
            break

    episode_date = parse_date(text)

    host_match = re.search(
        r"(主持人?|嘉賓)\s*[:：]\s*(.*?)(?:\d{2}/\d{2}/\d{4}|播放|重溫|足本|第一部份|第一部分|第二部份|第二部分|#|$)",
        text,
    )

    hosts = clean(host_match.group(2)) if host_match else ""

    programme = "unknown"
    if "free_as_the_wind_sunday" in episode_url:
        programme = "sunday"
    elif "Free_as_the_wind" in episode_url:
        programme = "monday"

    matched = KEYWORD in hosts

    return {
        "date": episode_date.isoformat() if episode_date else "",
        "programme": programme,
        "title": title,
        "hosts": hosts,
        "matched": matched,
        "episode_url": episode_url,
        "error": "",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start).date()
    end = datetime.fromisoformat(args.end).date()

    os.makedirs("output", exist_ok=True)

    episode_urls = search_episode_urls()

    all_rows = []
    matched_rows = []

    for url in episode_urls:
        try:
            row = extract_detail(url)

            if not row["date"]:
                row["error"] = "date_not_found"
                all_rows.append(row)
                continue

            d = datetime.fromisoformat(row["date"]).date()

            if start <= d <= end:
                all_rows.append(row)
                if row["matched"]:
                    matched_rows.append(row)

        except Exception as e:
            all_rows.append({
                "date": "",
                "programme": "unknown",
                "title": "",
                "hosts": "",
                "matched": False,
                "episode_url": url,
                "error": str(e),
            })

    fields = ["date", "programme", "title", "hosts", "matched", "episode_url", "error"]

    with open("output/all_episodes.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_rows)

    with open("output/matched_episodes.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(matched_rows)

    print(f"Episode URLs found: {len(episode_urls)}")
    print(f"All rows in date range: {len(all_rows)}")
    print(f"Matched rows: {len(matched_rows)}")


if __name__ == "__main__":
    main()

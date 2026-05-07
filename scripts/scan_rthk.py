import argparse
import csv
import os
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


PROGRAMS = {
    "sunday": "https://www.rthk.hk/radio/radio1/programme/free_as_the_wind_sunday",
    "monday": "https://www.rthk.hk/radio/radio1/programme/Free_as_the_wind",
}

KEYWORD = "馬鼎盛"


def fetch(url):
    headers = {
        "User-Agent": "Mozilla/5.0 RTHK personal archive scanner"
    }
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.text


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def parse_date(text):
    patterns = [
        r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})",
        r"(\d{1,2})/(\d{1,2})/(\d{4})",
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


def extract_episode_links(program_url):
    html = fetch(program_url)
    soup = BeautifulSoup(html, "lxml")

    links = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/episode/" in href:
            links.add(urljoin(program_url, href))

    return sorted(links)


def extract_detail(episode_url):
    html = fetch(episode_url)
    soup = BeautifulSoup(html, "lxml")

    text = clean(soup.get_text("\n"))

    title = ""
    h1 = soup.find(["h1", "h2", "h3"])
    if h1:
        title = clean(h1.get_text())

    episode_date = parse_date(text)

    host_match = re.search(
        r"(?:主持|主持人)\s*[:：]\s*(.*?)(?:\s{2,}|播放|重溫|足本|第一部份|第一部分|$)",
        text,
    )

    hosts = clean(host_match.group(1)) if host_match else ""

    return {
        "date": episode_date.isoformat() if episode_date else "",
        "title": title,
        "hosts": hosts,
        "matched": KEYWORD in hosts,
        "episode_url": episode_url,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start).date()
    end = datetime.fromisoformat(args.end).date()

    os.makedirs("output", exist_ok=True)

    all_rows = []
    matched_rows = []

    for programme_name, programme_url in PROGRAMS.items():
        for episode_url in extract_episode_links(programme_url):
            try:
                row = extract_detail(episode_url)
            except Exception as e:
                row = {
                    "date": "",
                    "title": "",
                    "hosts": "",
                    "matched": False,
                    "episode_url": episode_url,
                    "error": str(e),
                    "programme": programme_name,
                }
                all_rows.append(row)
                continue

            row["programme"] = programme_name
            row["error"] = ""

            if not row["date"]:
                all_rows.append(row)
                continue

            d = datetime.fromisoformat(row["date"]).date()

            if start <= d <= end:
                all_rows.append(row)
                if row["matched"]:
                    matched_rows.append(row)

    fields = ["date", "programme", "title", "hosts", "matched", "episode_url", "error"]

    with open("output/all_episodes.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_rows)

    with open("output/matched_episodes.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(matched_rows)

    print(f"All episodes: {len(all_rows)}")
    print(f"Matched episodes: {len(matched_rows)}")


if __name__ == "__main__":
    main()

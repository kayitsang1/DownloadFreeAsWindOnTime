import argparse
import csv
import os
import re
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup


KEYWORD = "馬鼎盛"

PROGRAMS = [
    "https://www.rthk.hk/radio/radio1/programme/free_as_the_wind_sunday",
    "https://www.rthk.hk/radio/radio1/programme/Free_as_the_wind"
]


def fetch(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.text


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def parse_date(text):
    patterns = [
        r"(\d{2})/(\d{2})/(\d{4})",
        r"(\d{4})-(\d{1,2})-(\d{1,2})",
    ]

    for p in patterns:
        m = re.search(p, text)
        if not m:
            continue

        parts = m.groups()

        if len(parts[0]) == 4:
            y, mo, d = map(int, parts)
        else:
            d, mo, y = map(int, parts)

        return datetime(y, mo, d).date()

    return None


def extract_episode_urls(program_url):
    html = fetch(program_url)
    soup = BeautifulSoup(html, "html.parser")

    urls = []

    for a in soup.find_all("a", href=True):
        href = a["href"]

        if "/episode/" in href:
            if href.startswith("/"):
                href = "https://www.rthk.hk" + href

            if href not in urls:
                urls.append(href)

    print(f"[{program_url}] Found {len(urls)} episode links")
    return urls


def extract_hosts(text):
    lines = [clean(line) for line in text.splitlines() if clean(line)]

    for i, line in enumerate(lines):
        if "主持" in line and ("：" in line or ":" in line):
            value = re.sub(r"^.*?主持人?\s*[:：]\s*", "", line)
            return clean(value)

        if line in ["主持", "主持人"] and i + 1 < len(lines):
            return clean(lines[i + 1])

    return ""


def extract_detail(url):
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")

    text = soup.get_text("\n")
    flat = clean(text)

    date = parse_date(flat)
    hosts = extract_hosts(text)

    title = ""
    for tag in soup.find_all(["h1", "h2", "h3"]):
        t = clean(tag.get_text())
        if t and "講東講西" not in t:
            title = t
            break

    programme = "sunday" if "sunday" in url else "monday"

    matched = KEYWORD in hosts

    print("----")
    print("URL:", url)
    print("DATE:", date)
    print("TITLE:", title)
    print("HOSTS:", hosts)
    print("MATCHED:", matched)

    return {
        "date": date.isoformat() if date else "",
        "programme": programme,
        "title": title,
        "hosts": hosts,
        "matched": matched,
        "episode_url": url,
        "error": "",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()

    print("RUNNING FINAL VERSION")

    start = datetime.fromisoformat(args.start).date()
    end = datetime.fromisoformat(args.end).date()

    os.makedirs("output", exist_ok=True)

    episode_urls = []

    for p in PROGRAMS:
        urls = extract_episode_urls(p)
        episode_urls.extend(urls)

    episode_urls = list(set(episode_urls))
    print("Total episode URLs:", len(episode_urls))

    all_rows = []
    matched_rows = []

    for url in episode_urls:
        try:
            row = extract_detail(url)

            if not row["date"]:
                continue

            d = datetime.fromisoformat(row["date"]).date()

            if start <= d <= end:
                all_rows.append(row)

                if row["matched"]:
                    matched_rows.append(row)

        except Exception as e:
            print("ERROR:", url, e)

        time.sleep(1)

    fields = ["date", "programme", "title", "hosts", "matched", "episode_url", "error"]

    with open("output/all_episodes.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_rows)

    with open("output/matched_episodes.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(matched_rows)

    print("==== SUMMARY ====")
    print("All:", len(all_rows))
    print("Matched:", len(matched_rows))


if __name__ == "__main__":
    main()

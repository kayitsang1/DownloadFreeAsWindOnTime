import argparse
import csv
import os
import re
import time
from datetime import datetime
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup


KEYWORD = "馬鼎盛"

SEARCH_QUERIES = [
    "講東講西 馬鼎盛",
    "講東講西 週日版 馬鼎盛",
    "講東講西 主持人 馬鼎盛",
    "Free_as_the_wind 馬鼎盛",
    "free_as_the_wind_sunday 馬鼎盛",
]

ALLOWED_PATTERNS = [
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
        r"(\d{4})-(\d{1,2})-(\d{1,2})",
        r"(\d{4})/(\d{1,2})/(\d{1,2})",
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


def is_valid_episode_url(url):
    return any(pattern in url for pattern in ALLOWED_PATTERNS)


def normalize_url(url):
    url = url.split("?")[0]
    url = url.split("#")[0]
    return url


def search_episode_urls():
    urls = set()

    for query in SEARCH_QUERIES:
        encoded = quote(query)
        search_url = f"https://websearch.rthk.hk/search?lang=tc&query={encoded}"

        print(f"Searching: {search_url}")

        try:
            html = fetch(search_url)
        except Exception as e:
            print(f"Search failed: {query} / {e}")
            continue

        soup = BeautifulSoup(html, "html.parser")

        for a in soup.find_all("a", href=True):
            href = urljoin(search_url, a["href"])
            href = normalize_url(href)

            if is_valid_episode_url(href):
                urls.add(href)

        time.sleep(1)

    print(f"Episode URLs found from search: {len(urls)}")
    for u in sorted(urls):
        print("FOUND:", u)

    return sorted(urls)


def extract_title(soup, text):
    candidates = []

    for tag in soup.find_all(["h1", "h2", "h3"]):
        value = clean(tag.get_text())
        if value:
            candidates.append(value)

    for c in candidates:
        if "講東講西" not in c and len(c) > 2:
            return c

    lines = [clean(x) for x in text.splitlines() if clean(x)]
    for line in lines:
        if "主持" not in line and "馬鼎盛" not in line and len(line) > 2:
            return line

    return ""


def extract_hosts(text):
    lines = [clean(line) for line in text.splitlines() if clean(line)]

    for i, line in enumerate(lines):
        if line in ["主持", "主持人", "主持：", "主持人："]:
            if i + 1 < len(lines):
                return clean(lines[i + 1])

        if "主持" in line and ("：" in line or ":" in line):
            value = re.sub(r"^.?主持人?\s[:：]\s*", "", line)
            return clean(value)

    m = re.search(
        r"主持人?\s*[:：]\s*(.*?)(?:播放|重溫|足本|第一部份|第一部分|第二部份|第二部分|$)",
        text,
        re.S,
    )

    if m:
        return clean(m.group(1))

    return ""


def extract_detail(episode_url):
    html = fetch(episode_url)
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")

    flat_text = clean(text)

    date = parse_date(flat_text)
    title = extract_title(soup, text)
    hosts = extract_hosts(text)

    programme = "unknown"
    if "free_as_the_wind_sunday" in episode_url:
        programme = "sunday"
    elif "Free_as_the_wind" in episode_url:
        programme = "monday"

    matched = KEYWORD in hosts

    print("----")
    print("URL:", episode_url)
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
        "episode_url": episode_url,
        "error": "",
    }


def write_csv(path, rows):
    fields = [
        "date",
        "programme",
        "title",
        "hosts",
        "matched",
        "episode_url",
        "error",
    ]

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


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
    error_rows = []

    for url in episode_urls:
        try:
            row = extract_detail(url)
        except Exception as e:
            row = {
                "date": "",
                "programme": "unknown",
                "title": "",
                "hosts": "",
                "matched": False,
                "episode_url": url,
                "error": str(e),
            }
            error_rows.append(row)
            all_rows.append(row)
            continue

        if not row["date"]:
            row["error"] = "date_not_found"
            all_rows.append(row)
            continue

        d = datetime.fromisoformat(row["date"]).date()

        if start <= d <= end:
            all_rows.append(row)

            if row["matched"]:
                matched_rows.append(row)

        time.sleep(1)

    write_csv("output/all_episodes.csv", all_rows)
    write_csv("output/matched_episodes.csv", matched_rows)
    write_csv("output/errors.csv", error_rows)

    print("==== SUMMARY ====")
    print(f"Episode URLs found: {len(episode_urls)}")
    print(f"All rows in date range: {len(all_rows)}")
    print(f"Matched rows: {len(matched_rows)}")
    print(f"Error rows: {len(error_rows)}")


if __name__ == "__main__":
    main()

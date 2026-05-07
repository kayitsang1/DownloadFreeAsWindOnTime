import argparse
import csv
import os
import re
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


KEYWORD = "馬鼎盛"

PROGRAMS = [
    {
        "name": "sunday",
        "url": "https://www.rthk.hk/radio/radio1/programme/free_as_the_wind_sunday",
        "path_key": "/radio/radio1/programme/free_as_the_wind_sunday",
    },
    {
        "name": "monday",
        "url": "https://www.rthk.hk/radio/radio1/programme/Free_as_the_wind",
        "path_key": "/radio/radio1/programme/Free_as_the_wind",
    },
]

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_url(url):
    url = url.split("#")[0]
    return url


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


def is_episode_url(url, path_key):
    return path_key in url and "/episode/" in url


def is_program_related_url(url, path_key):
    if "rthk.hk" not in url:
        return False

    if path_key not in url:
        return False

    if "/episode/" in url:
        return False

    return True


def extract_links_from_page(page_url, path_key):
    html = fetch(page_url)
    soup = BeautifulSoup(html, "html.parser")

    episode_urls = set()
    related_urls = set()

    for a in soup.find_all("a", href=True):
        href = normalize_url(urljoin(page_url, a["href"]))
        text = clean(a.get_text())

        if is_episode_url(href, path_key):
            episode_urls.add(href)

        elif is_program_related_url(href, path_key):
            related_urls.add(href)

        if any(x in text for x in ["2025", "2026", "更多", "重溫", "10", "11", "12", "01", "02", "03"]):
            print("DEBUG LINK:", text, "=>", href)

    return episode_urls, related_urls


def collect_episode_urls(program):
    start_url = program["url"]
    path_key = program["path_key"]

    pages_to_visit = [start_url]
    visited_pages = set()
    all_episode_urls = set()

    print("==== COLLECTING:", program["name"], "====")

    while pages_to_visit:
        page_url = pages_to_visit.pop(0)
        page_url = normalize_url(page_url)

        if page_url in visited_pages:
            continue

        visited_pages.add(page_url)

        print("VISIT PAGE:", page_url)

        try:
            episode_urls, related_urls = extract_links_from_page(page_url, path_key)
        except Exception as e:
            print("PAGE ERROR:", page_url, e)
            continue

        for u in episode_urls:
            all_episode_urls.add(u)

        for u in related_urls:
            if u not in visited_pages and u not in pages_to_visit:
                pages_to_visit.append(u)

        time.sleep(1)

        if len(visited_pages) > 40:
            print("STOP: too many pages visited")
            break

    print(program["name"], "episode URLs:", len(all_episode_urls))

    for u in sorted(all_episode_urls):
        print("EPISODE:", u)

    return sorted(all_episode_urls)


def extract_hosts(text):
    lines = [clean(line) for line in text.splitlines() if clean(line)]

    for i, line in enumerate(lines):
        if "主持" in line and ("：" in line or ":" in line):
            value = re.sub(r"^.*?主持人?\s*[:：]\s*", "", line)
            return clean(value)

        if line in ["主持", "主持人"] and i + 1 < len(lines):
            return clean(lines[i + 1])

    return ""


def extract_title(soup):
    for tag in soup.find_all(["h1", "h2", "h3"]):
        t = clean(tag.get_text())
        if t and "講東講西" not in t and t != "電視":
            return t

    return ""


def extract_detail(url, programme_name):
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")

    text = soup.get_text("\n")
    flat = clean(text)

    date = parse_date(flat)
    title = extract_title(soup)
    hosts = extract_hosts(text)
    matched = KEYWORD in hosts

    print("----")
    print("URL:", url)
    print("DATE:", date)
    print("TITLE:", title)
    print("HOSTS:", hosts)
    print("MATCHED:", matched)

    return {
        "date": date.isoformat() if date else "",
        "programme": programme_name,
        "title": title,
        "hosts": hosts,
        "matched": matched,
        "episode_url": url,
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

    print("RUNNING HISTORICAL CRAWLER VERSION")

    start = datetime.fromisoformat(args.start).date()
    end = datetime.fromisoformat(args.end).date()

    os.makedirs("output", exist_ok=True)

    all_rows = []
    matched_rows = []
    error_rows = []

    for program in PROGRAMS:
        episode_urls = collect_episode_urls(program)

        for url in episode_urls:
            try:
                row = extract_detail(url, program["name"])
            except Exception as e:
                row = {
                    "date": "",
                    "programme": program["name"],
                    "title": "",
                    "hosts": "",
                    "matched": False,
                    "episode_url": url,
                    "error": str(e),
                }

                error_rows.append(row)
                all_rows.append(row)
                print("DETAIL ERROR:", url, e)
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
    print("All rows:", len(all_rows))
    print("Matched rows:", len(matched_rows))
    print("Error rows:", len(error_rows))


if __name__ == "__main__":
    main()

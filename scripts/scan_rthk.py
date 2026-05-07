import argparse
import csv
import os
import re
import time
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


KEYWORDS = ["馬鼎盛", "马鼎盛"]

PROGRAMS = [
    {
        "name": "sunday",
        "url": "https://www.rthk.hk/radio/radio1/programme/free_as_the_wind_sunday",
        "base": "https://www.rthk.hk/radio/radio1/programme/free_as_the_wind_sunday/episode/",
    },
    {
        "name": "monday",
        "url": "https://www.rthk.hk/radio/radio1/programme/Free_as_the_wind",
        "base": "https://www.rthk.hk/radio/radio1/programme/Free_as_the_wind/episode/",
    },
]

HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
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


def extract_episode_urls_from_html(program):
    html = fetch(program["url"])
    urls = set()

    # 1. 正常 href episode link
    for m in re.finditer(r"/episode/(\d+)", html):
        episode_id = m.group(1)
        urls.add(program["base"] + episode_id)

    # 2. JS / data attribute 裡可能出現 episode id
    for m in re.finditer(r"(?:episode|episode_id|pid|eid)[\"'\s:=]+(\d{6,})", html, re.I):
        episode_id = m.group(1)
        urls.add(program["base"] + episode_id)

    # 3. RTHK 頁面常把 episode id 藏在含日期附近的 HTML 區塊
    date_blocks = re.finditer(
        r"(\d{2}/\d{2}/\d{4})(.{0,2500}?)(\d{6,})",
        html,
        re.S,
    )

    for m in date_blocks:
        episode_id = m.group(3)
        urls.add(program["base"] + episode_id)

    print(f"{program['name']} raw episode URLs:", len(urls))

    for u in sorted(urls):
        print("EPISODE:", u)

    return sorted(urls)


def extract_hosts(text):
    lines = [clean(line) for line in text.splitlines() if clean(line)]

    for i, line in enumerate(lines):
        if "主持" in line and ("：" in line or ":" in line):
            value = re.sub(r"^.*?主持人?\s*[:：]\s*", "", line)
            return clean(value)

        if line in ["主持", "主持人"] and i + 1 < len(lines):
            return clean(lines[i + 1])

    return ""


def extract_title(soup, text):
    lines = [clean(line) for line in text.splitlines() if clean(line)]

    for tag in soup.find_all(["h1", "h2", "h3"]):
        t = clean(tag.get_text())
        if t and t not in ["電視", "电视", "講東講西", "講東講西 (星期一至五)"]:
            return t

    # 後備：找日期後面的第一個像題目的行
    for i, line in enumerate(lines):
        if re.match(r"\d{2}/\d{2}/\d{4}", line):
            for candidate in lines[i + 1:i + 6]:
                if candidate and not any(x in candidate for x in ["主持", "足本", "第一部份", "第二部份"]):
                    return candidate

    return ""


def extract_detail(url, programme_name):
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")
    flat = clean(text)

    date = parse_date(flat)
    title = extract_title(soup, text)
    hosts = extract_hosts(text)
    matched = any(k in hosts for k in KEYWORDS)

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
    fields = ["date", "programme", "title", "hosts", "matched", "episode_url", "error"]

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()

    print("RUNNING EPISODE-ID PARSER VERSION")

    start = datetime.fromisoformat(args.start).date()
    end = datetime.fromisoformat(args.end).date()

    os.makedirs("output", exist_ok=True)

    all_rows = []
    matched_rows = []
    error_rows = []

    for program in PROGRAMS:
        episode_urls = extract_episode_urls_from_html(program)

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

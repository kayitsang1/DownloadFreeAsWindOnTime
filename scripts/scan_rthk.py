import argparse
import csv
import os
import re
import time
from datetime import datetime

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

    # 正常 href episode link
    for m in re.finditer(r"/episode/(\d+)", html):
        episode_id = m.group(1)
        urls.add(program["base"] + episode_id)

    # JS / data attribute 裡可能出現 episode id
    for m in re.finditer(r"(?:episode|episode_id|pid|eid)[\"'\s:=]+(\d{6,})", html, re.I):
        episode_id = m.group(1)
        urls.add(program["base"] + episode_id)

    # 日期附近可能藏 episode id
    for m in re.finditer(r"(\d{2}/\d{2}/\d{4})(.{0,2500}?)(\d{6,})", html, re.S):
        episode_id = m.group(3)
        urls.add(program["base"] + episode_id)

    print(f"{program['name']} raw episode URLs:", len(urls))

    for u in sorted(urls):
        print("EPISODE:", u)

    return sorted(urls)


def is_noise_line(line):
    noise = {
        "講東講西",
        "講東講西 - 週日版",
        "講東講西 (星期一至五)",
        "所有集數",
        "電視",
        "电视",
        "電台",
        "最新",
        "重溫",
        "CATCHUP",
        "LATEST",
        "GIST",
        "足本 Full",
        "第一部份 Part 1",
        "第二部份 Part 2",
    }

    if line in noise:
        return True

    if re.match(r"^\d{2}/\d{2}/\d{4}", line):
        return True

    if "主持" in line:
        return True

    return False


def extract_hosts_and_index(lines):
    host_index = -1
    hosts = ""

    # 取最後一個主持行，避免抓到頁面前面的最新 / 重溫列表
    for i, line in enumerate(lines):
        if "主持" in line and ("：" in line or ":" in line):
            value = re.sub(r"^.*?主持人?\s*[:：]\s*", "", line)
            value = clean(value)

            if value:
                host_index = i
                hosts = value

    return hosts, host_index


def extract_title_near_host(lines, host_index):
    if host_index == -1:
        return ""

    # 標題通常在主持人上一兩行
    for j in range(host_index - 1, -1, -1):
        candidate = clean(lines[j])

        if not candidate:
            continue

        if is_noise_line(candidate):
            continue

        return candidate

    return ""


def extract_date_near_host(lines, host_index):
    if host_index == -1:
        return None

    # 日期通常在主持人後面二十行內
    after_host = " ".join(lines[host_index:host_index + 25])
    date = parse_date(after_host)

    if date:
        return date

    # 有些頁面日期在主持人之前
    before_host = " ".join(lines[max(0, host_index - 10):host_index + 1])
    return parse_date(before_host)


def extract_detail(url, programme_name):
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")

    text = soup.get_text("\n")
    lines = [clean(line) for line in text.splitlines() if clean(line)]

    hosts, host_index = extract_hosts_and_index(lines)
    title = extract_title_near_host(lines, host_index)
    date = extract_date_near_host(lines, host_index)

    # 後備：如果附近找不到日期，才掃全文
    if not date:
        date = parse_date(clean(text))

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

    print("RUNNING DETAIL-PARSER FIX VERSION")

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

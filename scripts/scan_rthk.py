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
        "programme_code": "free_as_the_wind_sunday",
        "home_url": "https://www.rthk.hk/radio/radio1/programme/free_as_the_wind_sunday",
        "episode_base": "https://www.rthk.hk/radio/radio1/programme/free_as_the_wind_sunday/episode/",
    },
    {
        "name": "monday",
        "programme_code": "Free_as_the_wind",
        "home_url": "https://www.rthk.hk/radio/radio1/programme/Free_as_the_wind",
        "episode_base": "https://www.rthk.hk/radio/radio1/programme/Free_as_the_wind/episode/",
    },
]

CATCHUP_BASE = "https://www.rthk.hk/radio/radio1/programme/catchUp"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.rthk.hk/",
}


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


def extract_episode_ids_from_html(html):
    episode_ids = set()

    # 主要來源：data-episode="1096938"
    for m in re.finditer(r'data-episode=["\'](\d+)["\']', html):
        episode_ids.add(m.group(1))

    # 後備：/episode/1096938
    for m in re.finditer(r"/episode/(\d+)", html):
        episode_ids.add(m.group(1))

    # 後備：episode_id / eid / pid 之類
    for m in re.finditer(r"(?:episode|episode_id|pid|eid)[\"'\s:=]+(\d{6,})", html, re.I):
        episode_ids.add(m.group(1))

    return episode_ids


def collect_episode_urls(program, max_pages):
    urls = set()

    print("==== COLLECTING:", program["name"], "====")

    # 第 1 頁：首頁
    print("FETCH HOME:", program["home_url"])
    try:
        html = fetch(program["home_url"])
        ids = extract_episode_ids_from_html(html)
        print("FOUND HOME:", len(ids))
        for episode_id in ids:
            urls.add(program["episode_base"] + episode_id)
    except Exception as e:
        print("HOME FETCH ERROR:", e)

    # 第 2 頁起：catchUp API
    empty_or_duplicate_count = 0

    for page in range(2, max_pages + 1):
        api_url = (
            f"{CATCHUP_BASE}"
            f"?c=radio1"
            f"&p={program['programme_code']}"
            f"&page={page}"
        )

        print("FETCH CATCHUP:", api_url)

        try:
            html = fetch(api_url)
        except Exception as e:
            print("CATCHUP FETCH ERROR:", api_url, e)
            empty_or_duplicate_count += 1
            if empty_or_duplicate_count >= 2:
                break
            continue

        ids = extract_episode_ids_from_html(html)
        print(f"FOUND PAGE {page}:", len(ids))

        before = len(urls)

        for episode_id in ids:
            urls.add(program["episode_base"] + episode_id)

        after = len(urls)

        if len(ids) == 0:
            empty_or_duplicate_count += 1
        elif after == before:
            empty_or_duplicate_count += 1
        else:
            empty_or_duplicate_count = 0

        # 連續兩頁沒有新資料，就停
        if empty_or_duplicate_count >= 2:
            print("STOP: no new episodes for 2 pages")
            break

        time.sleep(0.5)

    print(program["name"], "total episode URLs:", len(urls))

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

    # 取最後一個主持行，避免抓到頁面前方的最新 / 重溫列表
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

    after_host = " ".join(lines[host_index:host_index + 25])
    date = parse_date(after_host)

    if date:
        return date

    before_host = " ".join(lines[max(0, host_index - 10):host_index + 1])
    date = parse_date(before_host)

    if date:
        return date

    return None


def extract_detail(url, programme_name):
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")

    text = soup.get_text("\n")
    lines = [clean(line) for line in text.splitlines() if clean(line)]

    hosts, host_index = extract_hosts_and_index(lines)
    title = extract_title_near_host(lines, host_index)
    date = extract_date_near_host(lines, host_index)

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
    parser.add_argument("--max-pages", type=int, default=30)
    args = parser.parse_args()

    print("RUNNING CATCHUP API VERSION")

    start = datetime.fromisoformat(args.start).date()
    end = datetime.fromisoformat(args.end).date()

    os.makedirs("output", exist_ok=True)

    all_rows = []
    matched_rows = []
    error_rows = []

    seen_urls = set()

    for program in PROGRAMS:
        episode_urls = collect_episode_urls(
            program,
            max_pages=args.max_pages,
        )

        for url in episode_urls:
            if url in seen_urls:
                continue

            seen_urls.add(url)

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

            time.sleep(0.5)

    all_rows.sort(key=lambda r: (r["date"], r["programme"], r["title"]))
    matched_rows.sort(key=lambda r: (r["date"], r["programme"], r["title"]))
    error_rows.sort(key=lambda r: r["episode_url"])

    write_csv("output/all_episodes.csv", all_rows)
    write_csv("output/matched_episodes.csv", matched_rows)
    write_csv("output/errors.csv", error_rows)

    print("==== SUMMARY ====")
    print("All rows:", len(all_rows))
    print("Matched rows:", len(matched_rows))
    print("Error rows:", len(error_rows))


if __name__ == "__main__":
    main()

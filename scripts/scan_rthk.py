import argparse
import csv
import html as html_lib
import json
import os
import re
import subprocess
import time
from datetime import date, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup


KEYWORDS = ["馬鼎盛", "马鼎盛"]

PROGRAMS = [
    {
        "name": "sunday",
        "programme_code": "free_as_the_wind_sunday",
        "home_url": "https://www.rthk.hk/radio/radio1/programme/free_as_the_wind_sunday",
        "episode_base": "https://www.rthk.hk/radio/radio1/programme/free_as_the_wind_sunday/episode/",
        "weekday": 6,  # Sunday
    },
    {
        "name": "monday",
        "programme_code": "Free_as_the_wind",
        "home_url": "https://www.rthk.hk/radio/radio1/programme/Free_as_the_wind",
        "episode_base": "https://www.rthk.hk/radio/radio1/programme/Free_as_the_wind/episode/",
        "weekday": 0,  # Monday
    },
]

CATCHUP_BASE = "https://www.rthk.hk/radio/catchUp"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}


def fetch(url, referer=None):
    headers = dict(HEADERS)
    if referer:
        headers["Referer"] = referer

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.text


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def parse_date(text):
    patterns = [
        r"(\d{2})/(\d{2})/(\d{4})",
        r"(\d{4})-(\d{1,2})-(\d{1,2})",
        r"(\d{4})/(\d{1,2})/(\d{1,2})",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue

        parts = match.groups()

        if len(parts[0]) == 4:
            year, month, day = map(int, parts)
        else:
            day, month, year = map(int, parts)

        return date(year, month, day)

    return None


def extract_episode_ids(raw):
    episode_ids = []

    try:
        data = json.loads(raw)

        if isinstance(data, dict):
            for item in data.get("content", []):
                if isinstance(item, dict):
                    episode_id = item.get("id")
                    if episode_id and str(episode_id).isdigit():
                        episode_ids.append(str(episode_id))

    except Exception:
        pass

    if episode_ids:
        return episode_ids

    candidates = [raw, html_lib.unescape(raw)]
    found = set()

    for text in candidates:
        for match in re.finditer(r'data-episode=["\'](\d+)["\']', text):
            found.add(match.group(1))

        for match in re.finditer(r"/episode/(\d+)", text):
            found.add(match.group(1))

        for match in re.finditer(
            r"(?:episode|episode_id|pid|eid|id)[\"'\s:=]+(\d{6,})",
            text,
            re.I,
        ):
            found.add(match.group(1))

    return sorted(found)


def fetch_home_episode_ids(program):
    print("FETCH HOME:", program["home_url"])
    raw = fetch(program["home_url"], referer=program["home_url"])
    ids = extract_episode_ids(raw)
    print("FOUND HOME:", len(ids))
    return ids


def fetch_catchup_episode_ids(program, page):
    url = (
        f"{CATCHUP_BASE}"
        f"?c=radio1"
        f"&p={program['programme_code']}"
        f"&page={page}"
        f"&m="
    )

    print("FETCH CATCHUP:", url)
    raw = fetch(url, referer=program["home_url"])
    ids = extract_episode_ids(raw)
    print(f"FOUND PAGE {page}:", len(ids))
    return ids


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

    for index, line in enumerate(lines):
        if "主持" in line and ("：" in line or ":" in line):
            value = re.sub(r"^.*?主持人?\s*[:：]\s*", "", line)
            value = clean(value)

            if value:
                host_index = index
                hosts = value

    return hosts, host_index


def extract_title_near_host(lines, host_index):
    if host_index == -1:
        return ""

    for index in range(host_index - 1, -1, -1):
        candidate = clean(lines[index])

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
    found_date = parse_date(after_host)

    if found_date:
        return found_date

    before_host = " ".join(lines[max(0, host_index - 10):host_index + 1])
    return parse_date(before_host)


def extract_detail(url, programme_name):
    html = fetch(url, referer=url)
    soup = BeautifulSoup(html, "html.parser")

    text = soup.get_text("\n")
    lines = [clean(line) for line in text.splitlines() if clean(line)]

    hosts, host_index = extract_hosts_and_index(lines)
    title = extract_title_near_host(lines, host_index)
    episode_date = extract_date_near_host(lines, host_index)

    if not episode_date:
        episode_date = parse_date(clean(text))

    matched = any(keyword in hosts for keyword in KEYWORDS)

    print("----")
    print("URL:", url)
    print("DATE:", episode_date)
    print("TITLE:", title)
    print("HOSTS:", hosts)
    print("MATCHED:", matched)

    return {
        "date": episode_date.isoformat() if episode_date else "",
        "programme": programme_name,
        "title": title,
        "hosts": hosts,
        "matched": matched,
        "filename": "",
        "episode_url": url,
        "download_status": "",
        "error": "",
    }


def download_mp3(episode_url, filename, download_dir):
    download_dir.mkdir(parents=True, exist_ok=True)

    output_template = str(download_dir / filename.replace(".mp3", ".%(ext)s"))

    command = [
        "yt-dlp",
        "--no-playlist",
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "0",
        "-o",
        output_template,
        episode_url,
    ]

    print("DOWNLOAD:", " ".join(command))
    subprocess.run(command, check=True)

    expected = download_dir / filename

    if expected.exists():
        return expected

    matches = list(download_dir.glob(filename.replace(".mp3", ".*")))

    if not matches:
        raise FileNotFoundError(f"Downloaded file not found: {filename}")

    if matches[0].suffix != ".mp3":
        new_path = download_dir / filename
        matches[0].rename(new_path)
        return new_path

    return matches[0]


def write_csv(path, rows):
    fields = [
        "date",
        "programme",
        "title",
        "hosts",
        "matched",
        "filename",
        "episode_url",
        "download_status",
        "error",
    ]

    with open(path, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def should_keep_program_day(program, episode_date):
    return episode_date.weekday() == program["weekday"]


def process_program(program, start_date, end_date, max_pages, seen_urls):
    print("==== COLLECTING:", program["name"], "====")

    all_rows = []
    matched_rows = []
    error_rows = []

    for page in range(1, max_pages + 1):
        if page == 1:
            episode_ids = fetch_home_episode_ids(program)
        else:
            episode_ids = fetch_catchup_episode_ids(program, page)

        if not episode_ids:
            print("STOP: no episode ids on page", page)
            break

        old_count = 0
        useful_count = 0

        for episode_id in episode_ids:
            url = program["episode_base"] + episode_id

            if url in seen_urls:
                continue

            seen_urls.add(url)

            try:
                row = extract_detail(url, program["name"])
            except Exception as error:
                row = {
                    "date": "",
                    "programme": program["name"],
                    "title": "",
                    "hosts": "",
                    "matched": False,
                    "filename": "",
                    "episode_url": url,
                    "download_status": "",
                    "error": str(error),
                }
                error_rows.append(row)
                all_rows.append(row)
                print("DETAIL ERROR:", url, error)
                continue

            if not row["date"]:
                row["error"] = "date_not_found"
                error_rows.append(row)
                all_rows.append(row)
                continue

            episode_date = datetime.fromisoformat(row["date"]).date()

            if episode_date < start_date:
                old_count += 1
                print("OLDER THAN START DATE:", episode_date, url)
                continue

            if episode_date > end_date:
                continue

            if not should_keep_program_day(program, episode_date):
                print("SKIP WRONG WEEKDAY:", episode_date, program["name"], url)
                continue

            useful_count += 1

            row["filename"] = episode_date.strftime("%m%d") + ".mp3"

            all_rows.append(row)

            if row["matched"]:
                matched_rows.append(row)

            time.sleep(0.3)

        if old_count > 0 and useful_count == 0:
            print("STOP PROGRAM: page is older than start date")
            break

        time.sleep(0.4)

    return all_rows, matched_rows, error_rows


def download_rows(rows, download_dir):
    for row in rows:
        if not row["matched"]:
            continue

        filename = row["filename"]
        episode_url = row["episode_url"]
        target = download_dir / filename

        if target.exists():
            row["download_status"] = "skipped_existing"
            print("SKIP EXISTING:", filename)
            continue

        try:
            download_mp3(episode_url, filename, download_dir)
            row["download_status"] = "downloaded"
        except Exception as error:
            row["download_status"] = "error"
            row["error"] = str(error)
            print("DOWNLOAD ERROR:", filename, error)

    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--download-dir", default="downloads")
    args = parser.parse_args()

    print("RUNNING RCLONE-READY MP3 VERSION")

    start_date = datetime.fromisoformat(args.start).date()
    end_date = datetime.fromisoformat(args.end).date()

    os.makedirs("output", exist_ok=True)

    all_rows = []
    matched_rows = []
    error_rows = []

    seen_urls = set()

    for program in PROGRAMS:
        program_all, program_matched, program_errors = process_program(
            program=program,
            start_date=start_date,
            end_date=end_date,
            max_pages=args.max_pages,
            seen_urls=seen_urls,
        )

        all_rows.extend(program_all)
        matched_rows.extend(program_matched)
        error_rows.extend(program_errors)

    all_rows.sort(key=lambda row: (row["date"], row["programme"], row["title"]))
    matched_rows.sort(key=lambda row: (row["date"], row["programme"], row["title"]))
    error_rows.sort(key=lambda row: row["episode_url"])

    if args.download:
        download_dir = Path(args.download_dir)
        matched_rows = download_rows(matched_rows, download_dir)

    write_csv("output/all_episodes.csv", all_rows)
    write_csv("output/matched_episodes.csv", matched_rows)
    write_csv("output/errors.csv", error_rows)

    print("==== SUMMARY ====")
    print("All rows:", len(all_rows))
    print("Matched rows:", len(matched_rows))
    print("Error rows:", len(error_rows))

    for row in matched_rows:
        print(
            row["date"],
            row["programme"],
            row["filename"],
            row["title"],
            row["download_status"],
        )


if __name__ == "__main__":
    main()

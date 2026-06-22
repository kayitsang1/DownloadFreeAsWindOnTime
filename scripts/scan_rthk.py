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


print("RUNNING RCLONE-READY MP3 VERSION WITH SUNDAY GENERIC HOST FIX")

KEYWORDS = ["馬鼎盛", "马鼎盛"]

SUNDAY_GENERIC_HOSTS = [
    "馬鼎盛",
    "馬恩賜",
    "文潔華",
    "海林",
    "蘇奭",
    "邱逸",
]

PROGRAMS = {
    "sunday": {
        "programme_code": "free_as_the_wind_sunday",
        "home_url": "https://www.rthk.hk/radio/radio1/programme/free_as_the_wind_sunday",
        "episode_base": "https://www.rthk.hk/radio/radio1/programme/free_as_the_wind_sunday/episode/",
        "weekday": 6,
    },
    "monday": {
        "programme_code": "Free_as_the_wind",
        "home_url": "https://www.rthk.hk/radio/radio1/programme/Free_as_the_wind",
        "episode_base": "https://www.rthk.hk/radio/radio1/programme/Free_as_the_wind/episode/",
        "weekday": 0,
    },
}

CATCHUP_BASE = "https://www.rthk.hk/radio/catchUp"


def fetch(url, referer=None):
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/html,application/xhtml+xml,application/xml,application/json;q=0.9,*/*;q=0.8",
        "X-Requested-With": "XMLHttpRequest",
    }

    if referer:
        headers["Referer"] = referer

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.text


def parse_date(value):
    if not value:
        return None

    value = value.strip()

    patterns = [
        "%d/%m/%Y",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ]

    for pattern in patterns:
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            pass

    return None


def extract_episode_ids(raw):
    episode_ids = []

    try:
        data = json.loads(raw)
        content = data.get("content", [])

        if isinstance(content, list):
            for item in content:
                episode_id = item.get("id")
                if episode_id:
                    episode_ids.append(str(episode_id))

        if episode_ids:
            return episode_ids
    except Exception:
        pass

    episode_ids.extend(re.findall(r'data-episode=["\']?(\d+)', raw))
    episode_ids.extend(re.findall(r"/episode/(\d+)", raw))

    seen = set()
    unique_ids = []

    for episode_id in episode_ids:
        if episode_id not in seen:
            seen.add(episode_id)
            unique_ids.append(episode_id)

    return unique_ids


def fetch_home_episode_ids(program):
    raw = fetch(program["home_url"])
    return extract_episode_ids(raw)


def fetch_catchup_episode_ids(program, max_pages):
    all_ids = []

    for page in range(1, max_pages + 1):
        params = (
            f"?c=radio1"
            f"&p={program['programme_code']}"
            f"&page={page}"
            f"&m="
        )
        url = CATCHUP_BASE + params

        try:
            raw = fetch(url, referer=program["home_url"])
        except Exception as exc:
            print(f"ERROR fetching catchUp page {page}: {exc}")
            continue

        ids = extract_episode_ids(raw)

        if not ids:
            break

        all_ids.extend(ids)
        time.sleep(0.3)

    seen = set()
    unique_ids = []

    for episode_id in all_ids:
        if episode_id not in seen:
            seen.add(episode_id)
            unique_ids.append(episode_id)

    return unique_ids


def normalize_text(raw_html):
    soup = BeautifulSoup(raw_html, "html.parser")

    for tag in soup(["script", "style"]):
        tag.decompose()

    text = soup.get_text("\n")
    text = html_lib.unescape(text)

    lines = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            lines.append(line)

    return lines


def extract_hosts_and_index(lines):
    host_line = ""
    host_index = -1

    for index, line in enumerate(lines):
        if "主持" not in line:
            continue

        if "：" not in line and ":" not in line:
            continue

        host_line = line
        host_index = index

    hosts = host_line

    hosts = re.sub(r"^.*?主持人?\s*[：:]\s*", "", hosts)
    hosts = hosts.strip()

    return hosts, host_index


def extract_title_near_host(lines, host_index):
    if host_index <= 0:
        return ""

    candidates = []

    start = max(0, host_index - 8)
    end = host_index

    for line in lines[start:end]:
        if "主持" in line:
            continue
        if "播放" in line:
            continue
        if re.search(r"\d{1,2}/\d{1,2}/\d{4}", line):
            continue
        if len(line) > 80:
            continue
        candidates.append(line)

    if candidates:
        return candidates[-1]

    return ""


def extract_date_near_host(lines, host_index):
    search_lines = []

    if host_index >= 0:
        start = max(0, host_index - 15)
        end = min(len(lines), host_index + 15)
        search_lines = lines[start:end]
    else:
        search_lines = lines

    for line in search_lines:
        match = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", line)
        if match:
            return parse_date(match.group(1))

    for line in lines:
        match = re.search(r"(\d{4}-\d{1,2}-\d{1,2})", line)
        if match:
            return parse_date(match.group(1))

    return None


def is_generic_sunday_hosts(hosts):
    normalized = hosts
    normalized = normalized.replace("主持人：", "")
    normalized = normalized.replace("主持：", "")
    normalized = normalized.replace(" ", "")
    normalized = normalized.replace("　", "")
    normalized = normalized.replace(",", "、")
    normalized = normalized.replace("，", "、")

    count = 0

    for name in SUNDAY_GENERIC_HOSTS:
        if name in normalized:
            count += 1

    return count >= 5


def extract_detail(programme_name, episode_url):
    raw = fetch(episode_url)
    lines = normalize_text(raw)

    hosts, host_index = extract_hosts_and_index(lines)
    title = extract_title_near_host(lines, host_index)
    episode_date = extract_date_near_host(lines, host_index)

    matched = any(keyword in hosts for keyword in KEYWORDS)

    if programme_name == "sunday" and is_generic_sunday_hosts(hosts):
        print("SUNDAY GENERIC HOST LIST DETECTED: treat as not matched")
        matched = False

    return {
        "date": episode_date.isoformat() if episode_date else "",
        "programme": programme_name,
        "title": title,
        "hosts": hosts,
        "matched": matched,
        "episode_url": episode_url,
        "error": "",
    }


def safe_filename_from_date(date_string):
    parsed = parse_date(date_string)

    if not parsed:
        try:
            parsed = datetime.strptime(date_string, "%Y-%m-%d").date()
        except ValueError:
            return "unknown"

    return parsed.strftime("%m%d")


def download_mp3(row, download_dir):
    episode_url = row["episode_url"]
    filename_base = safe_filename_from_date(row["date"])

    Path(download_dir).mkdir(parents=True, exist_ok=True)

    output_template = str(Path(download_dir) / f"{filename_base}.%(ext)s")

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

    print("DOWNLOADING:", episode_url)
    print("OUTPUT:", output_template)

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    if result.returncode != 0:
        print(result.stdout)
        raise RuntimeError(f"yt-dlp failed with exit code {result.returncode}")

    return f"{filename_base}.mp3"


def process_program(programme_name, program, start_date, end_date, max_pages):
    rows = []
    errors = []

    episode_ids = []

    try:
        episode_ids.extend(fetch_home_episode_ids(program))
    except Exception as exc:
        errors.append({
            "date": "",
            "programme": programme_name,
            "title": "",
            "hosts": "",
            "matched": False,
            "filename": "",
            "episode_url": program["home_url"],
            "download_status": "",
            "error": f"home fetch failed: {exc}",
        })

    try:
        episode_ids.extend(fetch_catchup_episode_ids(program, max_pages))
    except Exception as exc:
        errors.append({
            "date": "",
            "programme": programme_name,
            "title": "",
            "hosts": "",
            "matched": False,
            "filename": "",
            "episode_url": program["home_url"],
            "download_status": "",
            "error": f"catchUp fetch failed: {exc}",
        })

    seen = set()
    unique_episode_ids = []

    for episode_id in episode_ids:
        if episode_id not in seen:
            seen.add(episode_id)
            unique_episode_ids.append(episode_id)

    print(f"{programme_name}: found {len(unique_episode_ids)} unique episode IDs")

    for episode_id in unique_episode_ids:
        episode_url = program["episode_base"] + str(episode_id)

        try:
            row = extract_detail(programme_name, episode_url)

            if not row["date"]:
                continue

            episode_date = datetime.strptime(row["date"], "%Y-%m-%d").date()

            if episode_date < start_date or episode_date > end_date:
                continue

            if episode_date.weekday() != program["weekday"]:
                continue

            row["filename"] = safe_filename_from_date(row["date"]) + ".mp3"
            row["download_status"] = ""

            rows.append(row)

        except Exception as exc:
            errors.append({
                "date": "",
                "programme": programme_name,
                "title": "",
                "hosts": "",
                "matched": False,
                "filename": "",
                "episode_url": episode_url,
                "download_status": "",
                "error": str(exc),
            })

        time.sleep(0.3)

    return rows, errors


def write_csv(path, rows):
    fieldnames = [
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

    Path(path).parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()

        for row in rows:
            clean_row = {field: row.get(field, "") for field in fieldnames}
            writer.writerow(clean_row)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="Start date, YYYY-MM-DD")
    parser.add_argument("--end", default=date.today().isoformat(), help="End date, YYYY-MM-DD")
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--download-dir", default="downloads")

    args = parser.parse_args()

    start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.end, "%Y-%m-%d").date()

    all_rows = []
    all_errors = []

    for programme_name, program in PROGRAMS.items():
        rows, errors = process_program(
            programme_name=programme_name,
            program=program,
            start_date=start_date,
            end_date=end_date,
            max_pages=args.max_pages,
        )

        all_rows.extend(rows)
        all_errors.extend(errors)

    matched_rows = [row for row in all_rows if row.get("matched") is True]

    if args.download:
        for row in matched_rows:
            try:
                filename = download_mp3(row, args.download_dir)
                row["filename"] = filename
                row["download_status"] = "downloaded"
            except Exception as exc:
                row["download_status"] = "failed"
                row["error"] = str(exc)
                all_errors.append(row)

    write_csv("output/all_episodes.csv", all_rows)
    write_csv("output/matched_episodes.csv", matched_rows)
    write_csv("output/errors.csv", all_errors)

    print("DONE")
    print(f"all rows: {len(all_rows)}")
    print(f"matched rows: {len(matched_rows)}")
    print(f"errors: {len(all_errors)}")


if __name__ == "__main__":
    main()

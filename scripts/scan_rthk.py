import argparse
import csv
import json
import os
import re
import subprocess
import tempfile
import time
from datetime import date, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


KEYWORDS = ["馬鼎盛", "马鼎盛"]

PROGRAMS = [
    {
        "name": "sunday",
        "programme_code": "free_as_the_wind_sunday",
        "home_url": "https://www.rthk.hk/radio/radio1/programme/free_as_the_wind_sunday",
        "episode_base": "https://www.rthk.hk/radio/radio1/programme/free_as_the_wind_sunday/episode/",
        "weekday": 6,
    },
    {
        "name": "monday",
        "programme_code": "Free_as_the_wind",
        "home_url": "https://www.rthk.hk/radio/radio1/programme/Free_as_the_wind",
        "episode_base": "https://www.rthk.hk/radio/radio1/programme/Free_as_the_wind/episode/",
        "weekday": 0,
    },
]

CATCHUP = "https://www.rthk.hk/radio/catchUp"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}


def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def parse_date(text):
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", text)
    if m:
        d, mth, y = map(int, m.groups())
        return date(y, mth, d)
    return None


def extract_ids(raw):
    try:
        data = json.loads(raw)
        return [x["id"] for x in data.get("content", []) if "id" in x]
    except:
        return []


def extract_detail(url):
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")

    text = soup.get_text("\n")

    date_found = parse_date(text)

    host_match = re.search(r"主持[人]*[:：]\s*(.+)", text)
    hosts = host_match.group(1).strip() if host_match else ""

    title = ""
    for line in text.splitlines():
        if line.strip() and "主持" not in line and "講東講西" not in line:
            title = line.strip()
            break

    matched = any(k in hosts for k in KEYWORDS)

    return {
        "date": date_found,
        "title": title,
        "hosts": hosts,
        "matched": matched,
        "url": url,
    }


def build_drive():
    creds_json = json.loads(os.environ["GDRIVE_SERVICE_ACCOUNT_JSON"])
    creds = service_account.Credentials.from_service_account_info(
        creds_json,
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    return build("drive", "v3", credentials=creds)


def file_exists(service, folder, name):
    q = f"'{folder}' in parents and name='{name}' and trashed=false"
    res = service.files().list(q=q, fields="files(id)").execute()
    return res.get("files", [])


def upload(service, folder, filepath, filename):
    if file_exists(service, folder, filename):
        print("SKIP EXIST:", filename)
        return

    media = MediaFileUpload(filepath, mimetype="audio/mpeg")

    service.files().create(
        body={"name": filename, "parents": [folder]},
        media_body=media,
        fields="id",
    ).execute()

    print("UPLOADED:", filename)


def download_mp3(url, filename):
    tmp = tempfile.mkdtemp()
    out = str(Path(tmp) / "%(title)s.%(ext)s")

    cmd = [
        "yt-dlp",
        "-x",
        "--audio-format",
        "mp3",
        "-o",
        out,
        url,
    ]

    subprocess.run(cmd, check=True)

    for f in Path(tmp).glob("*.mp3"):
        return f


def run(start, end, max_pages, do_download):
    start = datetime.fromisoformat(start).date()
    end = datetime.fromisoformat(end).date()

    drive = build_drive() if do_download else None
    folder = os.environ.get("GDRIVE_FOLDER_ID")

    results = []

    for program in PROGRAMS:
        for page in range(1, max_pages + 1):
            if page == 1:
                raw = fetch(program["home_url"])
                ids = re.findall(r"data-episode=\"(\d+)\"", raw)
            else:
                url = f"{CATCHUP}?c=radio1&p={program['programme_code']}&page={page}&m="
                raw = fetch(url)
                ids = extract_ids(raw)

            if not ids:
                break

            for eid in ids:
                ep_url = program["episode_base"] + eid
                info = extract_detail(ep_url)

                if not info["date"]:
                    continue

                if info["date"] < start:
                    return results

                if info["date"] > end:
                    continue

                if info["date"].weekday() != program["weekday"]:
                    continue

                if not info["matched"]:
                    continue

                filename = info["date"].strftime("%m%d") + ".mp3"

                if do_download:
                    mp3 = download_mp3(ep_url, filename)
                    upload(drive, folder, mp3, filename)

                results.append(info)

                time.sleep(0.3)

    return results


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--max-pages", type=int, default=10)
    p.add_argument("--download", action="store_true")
    args = p.parse_args()

    print("RUNNING FINAL MP3 VERSION")

    run(args.start, args.end, args.max_pages, args.download)

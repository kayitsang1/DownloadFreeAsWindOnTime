import base64
import csv
import json
import os
import re
import subprocess
import time
from datetime import date
from pathlib import Path

import requests

TARGET_DATES = [
    "2026-05-17",
    "2026-05-24",
    "2026-05-31",
    "2026-06-21",
    "2026-06-28",
    "2026-07-12",
    "2026-07-19",
    "2026-08-16",
]

PROGRAMME_CODE = "free_as_the_wind_sunday"
HOME_URL = "https://www.rthk.hk/radio/radio1/programme/free_as_the_wind_sunday"
EPISODE_BASE = "https://www.rthk.hk/radio/radio1/programme/free_as_the_wind_sunday/episode/"
CATCHUP_BASE = "https://www.rthk.hk/radio/catchUp"

GEMINI_AUDIO_MODEL = "gemini-3.8-flash"

GEMINI_NAMES_PROMPT = """
你正在聆聽一段香港粵語電台節目。

請只根據音訊本身回答，不要猜測，也不要利用外部資料。

任務：
1. 找出節目開始時主持人介紹自己、其他主持或嘉賓的部分。
2. 列出你實際聽到的所有人名。
3. 自我介紹的人名也要列出，例如「小弟XXX」。
4. 對不確定的人名請標示「不確定」。
5. 不要因為某個名字可能很有名而自行補全。
6. 使用繁體中文。

最後格式：

聽到的人名：
- XXX
- XXX

關鍵原話：
「……」
""".strip()


def fetch(url, referer=None):
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/html,*/*;q=0.8",
        "X-Requested-With": "XMLHttpRequest",
    }
    if referer:
        headers["Referer"] = referer

    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.text


def parse_date(value):
    if not value:
        return None

    value = str(value).strip()

    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", value)
    if m:
        dd, mm, yyyy = m.groups()
        return date(int(yyyy), int(mm), int(dd))

    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", value)
    if m:
        yyyy, mm, dd = m.groups()
        return date(int(yyyy), int(mm), int(dd))

    return None


def get_sunday_episode_map(max_pages=40):
    episode_map = {}

    for page in range(1, max_pages + 1):
        url = (
            f"{CATCHUP_BASE}?c=radio1"
            f"&p={PROGRAMME_CODE}"
            f"&page={page}&m="
        )

        raw = fetch(url, referer=HOME_URL)

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            print(f"Page {page}: non-JSON response; stopping.")
            break

        content = payload.get("content", [])
        if not isinstance(content, list) or not content:
            print(f"Page {page}: no more episodes.")
            break

        for item in content:
            eid = item.get("id")
            d = parse_date(item.get("date"))
            title = str(item.get("title") or "").strip()

            if not eid or not d:
                continue

            episode_map[d.isoformat()] = {
                "episode_id": str(eid),
                "title": title,
                "episode_url": EPISODE_BASE + str(eid),
            }

        time.sleep(0.15)

    return episode_map


def get_drive_filenames():
    cmd = [
        "rclone",
        "lsf",
        "drive:freeaswind",
        "--files-only",
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    if result.returncode != 0:
        print(result.stdout)
        raise RuntimeError(
            f"rclone lsf failed with exit code {result.returncode}"
        )

    return {
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip()
    }


def drive_name_candidates(date_text):
    d = date.fromisoformat(date_text)

    return [
        f"{d:%Y%m%d}.mp3",
        f"{d:%m%d}.mp3",
    ]


def find_drive_file(date_text, drive_files):
    for name in drive_name_candidates(date_text):
        if name in drive_files:
            return name

    return ""


def download_episode_mp3(episode_url, date_text, workdir):
    workdir.mkdir(parents=True, exist_ok=True)

    base = date_text.replace("-", "")
    template = str(workdir / f"{base}.%(ext)s")

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "0",
        "-o",
        template,
        episode_url,
    ]

    print("DOWNLOADING:", episode_url)

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    if result.returncode != 0:
        print(result.stdout)
        raise RuntimeError(
            f"yt-dlp failed with exit code {result.returncode}"
        )

    expected = workdir / f"{base}.mp3"
    if expected.exists():
        return expected

    candidates = list(workdir.glob(f"{base}.*"))
    if not candidates:
        raise RuntimeError("Downloaded audio file was not found")

    return candidates[0]


def make_clip(mp3_path, clip_dir, seconds=180):
    clip_dir.mkdir(parents=True, exist_ok=True)

    clip = clip_dir / f"{mp3_path.stem}_first{seconds}s.mp3"

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(mp3_path),
        "-t",
        str(seconds),
        "-vn",
        "-acodec",
        "libmp3lame",
        str(clip),
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    if result.returncode != 0:
        print(result.stdout)
        raise RuntimeError(
            f"ffmpeg failed with exit code {result.returncode}"
        )

    return clip


def gemini_extract_names(audio_path, episode_title=""):
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    client = genai.Client(api_key=api_key)

    prompt = GEMINI_NAMES_PROMPT

    if episode_title:
        prompt += (
            "\n\n補充：這段音訊所屬節目的當集題目可能是："
            f"「{episode_title.strip()}」。"
            "這項資訊只用來幫助理解節目段落，不可據此猜測任何人名。"
        )

    audio_bytes = Path(audio_path).read_bytes()
    use_inline = len(audio_bytes) <= 13 * 1024 * 1024

    print(
        "GEMINI AUDIO INPUT MODE:",
        "inline" if use_inline else "files_api",
    )

    last_exc = None

    for attempt in range(1, 4):
        uploaded = None

        try:
            print(f"GEMINI C ATTEMPT {attempt}/3")

            if use_inline:
                audio_input = {
                    "type": "audio",
                    "data": base64.b64encode(audio_bytes).decode("utf-8"),
                    "mime_type": "audio/mp3",
                }
            else:
                uploaded = client.files.upload(file=str(audio_path))
                audio_input = {
                    "type": "audio",
                    "uri": uploaded.uri,
                    "mime_type": uploaded.mime_type or "audio/mp3",
                }

            interaction = client.interactions.create(
                model=GEMINI_AUDIO_MODEL,
                input=[
                    {
                        "type": "text",
                        "text": prompt,
                    },
                    audio_input,
                ],
                timeout=120,
            )

            result_text = (interaction.output_text or "").strip()

            if not result_text:
                raise RuntimeError(
                    "Gemini returned an empty audio analysis"
                )

            print("=" * 70)
            print(result_text)
            print("=" * 70)

            return result_text

        except Exception as exc:
            last_exc = exc
            print(
                "GEMINI C ERROR:",
                f"{type(exc).__name__}: {exc}",
            )

            if attempt < 3:
                wait_seconds = 15 * attempt
                print(
                    f"Retrying in {wait_seconds} seconds..."
                )
                time.sleep(wait_seconds)

        finally:
            if uploaded is not None:
                try:
                    client.files.delete(name=uploaded.name)
                except Exception as exc:
                    print(
                        "WARNING deleting Gemini temp file:",
                        exc,
                    )

    raise RuntimeError(
        "Gemini C failed after 3 attempts: "
        f"{type(last_exc).__name__}: {last_exc}"
    )


def target_found(result_text):
    text = re.sub(r"\s+", "", str(result_text or ""))

    return "馬鼎盛" in text or "马鼎盛" in text


def write_csv(rows):
    out = Path("audit_output")
    out.mkdir(parents=True, exist_ok=True)

    fields = [
        "date",
        "episode_id",
        "title",
        "episode_url",
        "drive_present",
        "drive_filename",
        "gemini_target_found",
        "status",
        "gemini_result",
        "error",
    ]

    path = out / "sunday_history_audit.csv"

    with path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

        for row in rows:
            writer.writerow(
                {field: row.get(field, "") for field in fields}
            )

    return path


def main():
    print("RTHK SUNDAY HISTORICAL AUDIT")
    print("Target dates:", ", ".join(TARGET_DATES))

    drive_files = get_drive_filenames()

    print("")
    print("Google Drive freeaswind files:")
    for name in sorted(drive_files):
        print("  ", name)

    episode_map = get_sunday_episode_map(max_pages=40)

    workdir = Path("audit_work")
    clip_dir = Path("audit_clips")
    result_dir = Path("audit_output")
    result_dir.mkdir(parents=True, exist_ok=True)

    rows = []

    for date_text in TARGET_DATES:
        print("")
        print("#" * 78)
        print("AUDITING:", date_text)
        print("#" * 78)

        drive_filename = find_drive_file(
            date_text,
            drive_files,
        )

        info = episode_map.get(date_text)

        row = {
            "date": date_text,
            "episode_id": "",
            "title": "",
            "episode_url": "",
            "drive_present": bool(drive_filename),
            "drive_filename": drive_filename,
            "gemini_target_found": "",
            "status": "",
            "gemini_result": "",
            "error": "",
        }

        if not info:
            row["status"] = "EPISODE_NOT_FOUND"
            row["error"] = (
                "Date not found in RTHK catchUp API "
                "within 40 pages"
            )
            rows.append(row)
            continue

        row.update(info)

        mp3_path = None
        clip_path = None

        try:
            mp3_path = download_episode_mp3(
                info["episode_url"],
                date_text,
                workdir,
            )

            clip_path = make_clip(
                mp3_path,
                clip_dir,
                seconds=180,
            )

            analysis = gemini_extract_names(
                clip_path,
                info["title"],
            )

            found = target_found(analysis)

            row["gemini_result"] = analysis
            row["gemini_target_found"] = found

            if found and drive_filename:
                row["status"] = "TARGET_PRESENT_AND_DRIVE_HAS_FILE"

            elif found and not drive_filename:
                row["status"] = "MISSING_FROM_DRIVE"

            elif not found and drive_filename:
                row["status"] = "DRIVE_FILE_BUT_TARGET_NOT_FOUND"

            else:
                row["status"] = "TARGET_NOT_FOUND"

            txt = result_dir / f"{date_text}_gemini.txt"
            txt.write_text(
                analysis,
                encoding="utf-8",
            )

        except Exception as exc:
            row["status"] = "ERROR"
            row["error"] = (
                f"{type(exc).__name__}: {exc}"
            )

        finally:
            # Historical audit only. Do not modify Google Drive.
            # Delete temporary full audio/clip from the runner.
            for path in [clip_path, mp3_path]:
                if path is not None:
                    try:
                        Path(path).unlink(missing_ok=True)
                    except Exception:
                        pass

        rows.append(row)

    csv_path = write_csv(rows)

    print("")
    print("=" * 78)
    print("FINAL AUDIT SUMMARY")
    print("=" * 78)

    for row in rows:
        print(
            f"{row['date']} | "
            f"Drive={row['drive_filename'] or 'MISSING'} | "
            f"Gemini={row['gemini_target_found']} | "
            f"{row['status']}"
        )

    missing = [
        row["date"]
        for row in rows
        if row["status"] == "MISSING_FROM_DRIVE"
    ]

    questionable = [
        row["date"]
        for row in rows
        if row["status"] in {
            "ERROR",
            "EPISODE_NOT_FOUND",
            "DRIVE_FILE_BUT_TARGET_NOT_FOUND",
        }
    ]

    print("")
    print("Missing confirmed 馬鼎盛 episodes:")
    print(", ".join(missing) if missing else "NONE")

    print("")
    print("Dates requiring manual review:")
    print(
        ", ".join(questionable)
        if questionable
        else "NONE"
    )

    print("")
    print("CSV:", csv_path)


if __name__ == "__main__":
    main()

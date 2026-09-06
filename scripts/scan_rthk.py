import argparse
import csv
import html as html_lib
import json
import os
import re
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

print("RUNNING RTHK MP3 - EPISODE DETAIL PARSER + GEMINI AUDIO UNDERSTANDING C")

KEYWORDS = ["馬鼎盛", "马鼎盛"]
PEOPLE_LABELS = ["主持人", "主持", "嘉賓", "嘉宾"]

SUNDAY_GENERIC_HOSTS = [
    "馬鼎盛", "馬恩賜", "文潔華", "海林", "蘇奭",
    "蘇頴", "邱逸", "鄧達智", "黃仲遠",
]

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
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.text


def parse_date(value):
    if not value:
        return None
    value = str(value).strip()

    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", value)
    if m:
        d, mo, y = m.groups()
        try:
            return date(int(y), int(mo), int(d))
        except ValueError:
            pass

    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", value)
    if m:
        y, mo, d = m.groups()
        try:
            return date(int(y), int(mo), int(d))
        except ValueError:
            pass
    return None


def date_variants(value):
    d = parse_date(value)
    if not d:
        return []
    return [
        d.strftime("%d/%m/%Y"),
        f"{d.day}/{d.month}/{d.year}",
        d.strftime("%Y-%m-%d"),
        d.strftime("%Y/%m/%d"),
    ]


def range_contains_weekday(start_date, end_date, weekday):
    d = start_date
    while d <= end_date:
        if d.weekday() == weekday:
            return True
        d += timedelta(days=1)
    return False


def extract_episode_meta_from_raw(raw):
    out = {}
    try:
        content = json.loads(raw).get("content", [])
        if isinstance(content, list):
            for item in content:
                eid = item.get("id")
                if eid:
                    out[str(eid)] = {
                        "api_title": str(item.get("title") or "").strip(),
                        "api_date": str(item.get("date") or "").strip(),
                    }
    except Exception:
        pass
    return out


def extract_episode_ids(raw):
    ids = []
    try:
        content = json.loads(raw).get("content", [])
        if isinstance(content, list):
            ids = [str(x["id"]) for x in content if x.get("id")]
        if ids:
            return list(dict.fromkeys(ids))
    except Exception:
        pass

    ids += re.findall(r'data-episode=["\']?(\d+)', raw)
    ids += re.findall(r"/episode/(\d+)", raw)
    return list(dict.fromkeys(ids))


def fetch_home_episode_ids(program):
    return extract_episode_ids(fetch(program["home_url"]))


def fetch_catchup_episode_ids(program, max_pages):
    ids = []
    meta = {}

    for page in range(1, max_pages + 1):
        url = (
            f"{CATCHUP_BASE}?c=radio1"
            f"&p={program['programme_code']}&page={page}&m="
        )
        try:
            raw = fetch(url, referer=program["home_url"])
        except Exception as exc:
            print(f"ERROR catchUp page {page}: {exc}")
            continue

        page_ids = extract_episode_ids(raw)
        meta.update(extract_episode_meta_from_raw(raw))

        if not page_ids:
            break

        ids.extend(page_ids)
        time.sleep(0.2)

    return list(dict.fromkeys(ids)), meta


def normalize_text(raw_html):
    soup = BeautifulSoup(raw_html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = html_lib.unescape(soup.get_text("\n"))
    return [x.strip() for x in text.splitlines() if x.strip()]


def normalize_anchor_text(text):
    text = html_lib.unescape(str(text or ""))
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[：:，,。.!！?？、\-–—_/／|()（）\[\]【】「」『』]", "", text)
    return text.lower()


def parse_people_line(line):
    m = re.match(r"^\s*(主持人|主持|嘉賓|嘉宾)\s*[：:]?\s*(.*?)\s*$", line)
    return (m.group(1), m.group(2).strip()) if m else (None, None)


def looks_like_stop_line(line):
    if not line:
        return True
    if parse_people_line(line)[0]:
        return True

    lower = line.strip().lower()
    if lower in {"最新", "latest", "節目重溫", "节目重温", "重溫", "重温", "更多", "more"}:
        return True
    if any(x in line for x in ["播放", "第一部份", "第二部份", "第一部分", "第二部分"]):
        return True
    if re.search(r"\d{1,2}/\d{1,2}/\d{4}", line):
        return True
    if re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", line):
        return True
    return len(line) > 100


def collect_multiline_people_text(lines, start, first_text):
    parts = [first_text] if first_text else []
    for i in range(start + 1, min(len(lines), start + 5)):
        line = lines[i].strip()
        if looks_like_stop_line(line):
            break
        parts.append(line)
    return "、".join(parts).strip("、 ")


def extract_people_items(lines):
    items = []
    for i, line in enumerate(lines):
        label, first = parse_people_line(line)
        if not label:
            continue
        text = collect_multiline_people_text(lines, i, first)
        if text:
            items.append({"label": label, "text": text, "index": i})
    return items


def split_people_names(text):
    names = []
    for part in re.split(r"[、,，/／|]+", str(text or "")):
        part = re.sub(r"[（(][^）)]*[）)]", "", part).strip()
        if not part or part.lower() in {"latest", "more"} or part in {"最新", "更多"}:
            continue
        names.append(part)
    return list(dict.fromkeys(names))


def people_names_from_items(items):
    names = []
    for item in items:
        names.extend(split_people_names(item["text"]))
    return list(dict.fromkeys(names))


def is_generic_sunday_people_text(text):
    compact = normalize_anchor_text(text)
    hits = sum(
        1 for name in SUNDAY_GENERIC_HOSTS
        if normalize_anchor_text(name) in compact
    )
    return hits >= 5


def find_episode_scope(lines, expected_date="", expected_title=""):
    title_indices = []
    date_indices = []

    title_key = normalize_anchor_text(expected_title)
    if title_key and len(title_key) >= 2:
        for i, line in enumerate(lines):
            key = normalize_anchor_text(line)
            if key and (title_key in key or key in title_key):
                title_indices.append(i)

    for variant in date_variants(expected_date):
        for i, line in enumerate(lines):
            if variant in line:
                date_indices.append(i)

    title_indices = list(dict.fromkeys(title_indices))
    date_indices = list(dict.fromkeys(date_indices))

    if title_indices and date_indices:
        t, d = min(
            ((t, d) for t in title_indices for d in date_indices),
            key=lambda x: abs(x[0] - x[1]),
        )
        if abs(t - d) <= 80:
            return max(0, min(t, d) - 20), min(len(lines) - 1, max(t, d) + 30), t, "title+date"

    if title_indices:
        a = title_indices[0]
        return max(0, a - 25), min(len(lines) - 1, a + 35), a, "title"

    if date_indices:
        a = date_indices[0]
        return max(0, a - 25), min(len(lines) - 1, a + 35), a, "date"

    return 0, -1, -1, "not_found"


def select_episode_people_items(programme_name, items, start, end, anchor):
    if end < start or anchor < 0:
        return [], False, "no_episode_anchor"

    scoped = [x for x in items if start <= x["index"] <= end]
    if not scoped:
        return [], False, "no_people_near_episode"

    if programme_name == "sunday":
        non_generic = [x for x in scoped if not is_generic_sunday_people_text(x["text"])]
        if non_generic:
            scoped = non_generic
        else:
            return scoped, False, "generic_roster_near_episode"

    if min(abs(x["index"] - anchor) for x in scoped) > 35:
        return [], False, "people_too_far_from_episode"

    nearest = min(scoped, key=lambda x: abs(x["index"] - anchor))["index"]
    block = [x for x in scoped if abs(x["index"] - nearest) <= 12]

    if not block:
        return [], False, "no_local_people_block"
    return block, True, "episode_detail"


def build_people_fields(items):
    hosts, guests, text = [], [], []

    for item in items:
        if item["label"] in {"主持人", "主持"}:
            hosts.append(item["text"])
        if item["label"] in {"嘉賓", "嘉宾"}:
            guests.append(item["text"])
        text.append(item["text"])

    names = people_names_from_items(items)
    return {
        "hosts": " | ".join(hosts),
        "guests": " | ".join(guests),
        "matched_text": " ".join(text),
        "people_count": len(names),
        "people_names": "、".join(names),
    }


def decide_match(programme_name, reliable_people, matched_text):
    page_match = any(x in matched_text for x in KEYWORDS)

    if programme_name == "sunday":
        if reliable_people:
            if page_match:
                return True, False, "direct_episode_name_match", True
            return False, False, "episode_people_no_target", False

        # Only when the episode-specific block cannot be trusted do we use Gemini.
        return True, True, "episode_people_unavailable_gemini_fallback", page_match

    if page_match:
        return True, False, "direct_episode_name_match", True
    return False, False, "episode_people_no_target", False


def extract_detail(programme_name, episode_url, expected_date="", expected_title=""):
    raw = fetch(episode_url)
    lines = normalize_text(raw)
    all_items = extract_people_items(lines)

    start, end, anchor, anchor_source = find_episode_scope(
        lines, expected_date, expected_title
    )

    selected, reliable, people_source = select_episode_people_items(
        programme_name, all_items, start, end, anchor
    )

    # Keep Monday compatibility with older page layouts.
    if programme_name == "monday" and not reliable and all_items:
        selected = all_items
        reliable = True
        people_source = "monday_whole_page_fallback"

    fields = build_people_fields(selected)
    matched, needs_verify, reason, page_match = decide_match(
        programme_name, reliable, fields["matched_text"]
    )

    fixed_roster = (
        programme_name == "sunday"
        and any(is_generic_sunday_people_text(x["text"]) for x in all_items)
    )

    d = parse_date(expected_date)

    row = {
        "date": d.isoformat() if d else "",
        "programme": programme_name,
        "title": expected_title.strip(),
        "hosts": fields["hosts"],
        "guests": fields["guests"],
        "people_count": fields["people_count"],
        "people_names": fields["people_names"],
        "page_name_matched": page_match,
        "matched": matched,
        "candidate_reason": reason,
        "needs_audio_verify": needs_verify,
        "episode_people_reliable": reliable,
        "people_source": people_source,
        "anchor_source": anchor_source,
        "fixed_roster_detected": fixed_roster,
        "filename": "",
        "episode_url": episode_url,
        "download_status": "",
        "audio_verified": "",
        "audio_transcript": "",
        "audio_match_groups": "",
        "error": "",
    }

    print(
        f"EPISODE DECISION: {programme_name} date={row['date'] or '?'} "
        f"anchor={anchor_source} source={people_source} "
        f"people={row['people_names'] or '(none)'} "
        f"page_match={page_match} candidate={matched} "
        f"verify={needs_verify} reason={reason}"
    )
    return row


def safe_filename_from_date(value):
    d = parse_date(value)
    return d.strftime("%m%d") if d else "unknown"


def download_mp3(row, download_dir):
    Path(download_dir).mkdir(parents=True, exist_ok=True)
    base = safe_filename_from_date(row["date"])
    template = str(Path(download_dir) / f"{base}.%(ext)s")

    cmd = [
        "yt-dlp", "--no-playlist", "-x",
        "--audio-format", "mp3", "--audio-quality", "0",
        "-o", template, row["episode_url"],
    ]

    print("DOWNLOADING:", row["episode_url"])
    print("OUTPUT:", template)

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.returncode != 0:
        print(result.stdout)
        raise RuntimeError(f"yt-dlp failed with exit code {result.returncode}")

    final = Path(download_dir) / f"{base}.mp3"
    if not final.exists():
        candidates = list(Path(download_dir).glob(f"{base}.*"))
        if not candidates:
            raise RuntimeError("yt-dlp reported success but output file was not found")
        final = candidates[0]
    return str(final)


def make_verify_clip(mp3_path, verify_dir, seconds):
    Path(verify_dir).mkdir(parents=True, exist_ok=True)
    source = Path(mp3_path)
    clip = Path(verify_dir) / f"{source.stem}_first{seconds}s.mp3"

    cmd = [
        "ffmpeg", "-y", "-i", str(source), "-t", str(seconds),
        "-vn", "-acodec", "libmp3lame", str(clip),
    ]

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.returncode != 0:
        print(result.stdout)
        raise RuntimeError(f"ffmpeg clip failed with exit code {result.returncode}")
    return str(clip)


def analyze_cantonese_audio_gemini(audio_path, episode_title=""):
    """
    Gemini Audio Understanding - C mode.

    不做完整逐字轉錄，也不直接提示「馬鼎盛」作 yes/no 判斷。
    只要求 Gemini 從音訊中抽取實際聽到的主持／嘉賓人名，
    再由本地程式檢查結果是否包含「馬鼎盛」。
    """
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

    uploaded = None

    try:
        print("UPLOADING VERIFY CLIP TO GEMINI:", audio_path)
        uploaded = client.files.upload(file=audio_path)

        audio_input = {
            "type": "audio",
            "uri": uploaded.uri,
            "mime_type": uploaded.mime_type,
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
        )

        result_text = (interaction.output_text or "").strip()

        print("")
        print("=" * 70)
        print("GEMINI AUDIO UNDERSTANDING - EXTRACT NAMES")
        print("=" * 70)
        print(result_text)
        print("=" * 70)
        print("")

        return result_text

    finally:
        if uploaded is not None:
            try:
                client.files.delete(name=uploaded.name)
                print("GEMINI TEMP FILE DELETED")
            except Exception as exc:
                print(f"WARNING deleting Gemini temp file: {exc}")


def normalize_gemini_result(text):
    return re.sub(
        r"[\s，,。.!！?？、：:；;「」『』（）()\[\]【】]",
        "",
        str(text or ""),
    )


def audio_analysis_mentions_target(result_text):
    """
    C 模式結果只作非常保守的精確名稱比對。

    若 Gemini 列出「馬鼎盛」或「马鼎盛」即視為通過。
    不再使用 maa5/ding2/sing4 同音字推測，避免增加 false positive。
    """
    normalized = normalize_gemini_result(result_text)

    for keyword in KEYWORDS:
        if keyword in normalized:
            return True, "gemini_c_exact_name"

    return False, "gemini_c_target_not_found"


def empty_error_row(programme, url, error):
    return {
        "date": "", "programme": programme, "title": "", "hosts": "", "guests": "",
        "people_count": "", "people_names": "", "page_name_matched": False,
        "matched": False, "candidate_reason": "", "needs_audio_verify": "",
        "episode_people_reliable": "", "people_source": "", "anchor_source": "",
        "fixed_roster_detected": "", "filename": "", "episode_url": url,
        "download_status": "", "audio_verified": "", "audio_transcript": "",
        "audio_match_groups": "", "error": error,
    }


def process_program(programme_name, program, start_date, end_date, max_pages):
    rows, errors = [], []
    ids, meta = [], {}

    try:
        catchup_ids, catchup_meta = fetch_catchup_episode_ids(program, max_pages)
        ids.extend(catchup_ids)
        meta.update(catchup_meta)
    except Exception as exc:
        errors.append(empty_error_row(programme_name, program["home_url"], f"catchUp failed: {exc}"))

    try:
        ids.extend(fetch_home_episode_ids(program))
    except Exception as exc:
        errors.append(empty_error_row(programme_name, program["home_url"], f"home failed: {exc}"))

    ids = list(dict.fromkeys(ids))
    print(f"{programme_name}: found {len(ids)} unique episode IDs")

    for eid in ids:
        item = meta.get(str(eid), {})
        api_date = parse_date(item.get("api_date", ""))
        api_title = item.get("api_title", "")

        # Skip old detail pages before fetching them.
        if api_date:
            if api_date < start_date or api_date > end_date:
                continue
            if api_date.weekday() != program["weekday"]:
                continue

        url = program["episode_base"] + str(eid)

        try:
            row = extract_detail(
                programme_name,
                url,
                expected_date=api_date.isoformat() if api_date else "",
                expected_title=api_title,
            )

            if not row["date"]:
                # Last-resort date scan for home-only IDs.
                raw = fetch(url)
                for line in normalize_text(raw):
                    d = parse_date(line)
                    if d:
                        row["date"] = d.isoformat()
                        break

            d = parse_date(row["date"])
            if not d:
                print("SKIP NO DATE:", url)
                continue
            if d < start_date or d > end_date or d.weekday() != program["weekday"]:
                continue

            row["filename"] = safe_filename_from_date(row["date"]) + ".mp3"
            rows.append(row)

        except Exception as exc:
            errors.append(empty_error_row(programme_name, url, str(exc)))

        time.sleep(0.2)

    return rows, errors


def write_csv(path, rows):
    fields = [
        "date", "programme", "title", "hosts", "guests", "people_count",
        "people_names", "page_name_matched", "matched", "candidate_reason",
        "needs_audio_verify", "episode_people_reliable", "people_source",
        "anchor_source", "fixed_roster_detected", "filename", "episode_url",
        "download_status", "audio_verified", "audio_transcript",
        "audio_match_groups", "error",
    ]

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--download-dir", default="downloads")
    parser.add_argument("--verify-sunday-audio", action="store_true")
    parser.add_argument("--verify-seconds", type=int, default=180)
    parser.add_argument("--verify-dir", default="verify_clips")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()

    all_rows, all_errors = [], []

    for programme_name, program in PROGRAMS.items():
        if not range_contains_weekday(start, end, program["weekday"]):
            print(f"SKIP PROGRAMME {programme_name}: no matching weekday in requested range")
            continue

        rows, errors = process_program(
            programme_name, program, start, end, args.max_pages
        )
        all_rows.extend(rows)
        all_errors.extend(errors)

    matched_rows = [x for x in all_rows if x.get("matched") is True]

    if args.download:
        for row in matched_rows:
            downloaded_file = None

            try:
                downloaded_path = download_mp3(row, args.download_dir)
                downloaded_file = Path(downloaded_path)
                row["filename"] = downloaded_file.name
                row["download_status"] = "downloaded"

                if row["programme"] == "sunday" and row.get("needs_audio_verify") is True:
                    if not args.verify_sunday_audio:
                        row["download_status"] = "verification_not_enabled"
                        if downloaded_file.exists():
                            downloaded_file.unlink()
                        print("SUNDAY NEEDS GEMINI BUT VERIFY FLAG IS OFF: deleted")
                        continue

                    print("VERIFYING SUNDAY AUDIO WITH GEMINI C MODE:", downloaded_path)
                    clip = make_verify_clip(
                        downloaded_path, args.verify_dir, args.verify_seconds
                    )
                    analysis_text = analyze_cantonese_audio_gemini(
                        clip, row.get("title", "")
                    )
                    row["audio_transcript"] = analysis_text

                    ok, groups = audio_analysis_mentions_target(analysis_text)
                    row["audio_match_groups"] = groups

                    if ok:
                        row["audio_verified"] = "true"
                        row["download_status"] = "downloaded_gemini_c_verified"
                        print("GEMINI C VERIFIED: keep file")
                    else:
                        row["audio_verified"] = "false"
                        row["download_status"] = "rejected_by_gemini_c_audio_check"
                        if downloaded_file.exists():
                            downloaded_file.unlink()
                        print("GEMINI C TARGET NOT FOUND: deleted file")

                elif row["programme"] == "sunday":
                    row["audio_verified"] = "not_required"

            except Exception as exc:
                row["download_status"] = "failed"
                row["error"] = str(exc)

                if (
                    row.get("programme") == "sunday"
                    and row.get("needs_audio_verify") is True
                    and downloaded_file is not None
                    and downloaded_file.exists()
                ):
                    downloaded_file.unlink()
                    print("VERIFY ERROR: unverified Sunday file deleted")

                all_errors.append(row.copy())

    write_csv("output/all_episodes.csv", all_rows)
    write_csv("output/matched_episodes.csv", matched_rows)
    write_csv("output/errors.csv", all_errors)

    print("DONE")
    print(f"all rows: {len(all_rows)}")
    print(f"matched rows: {len(matched_rows)}")
    print(f"errors: {len(all_errors)}")


if __name__ == "__main__":
    main()

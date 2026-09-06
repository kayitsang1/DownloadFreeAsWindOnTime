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


print(
    "RUNNING RCLONE-READY MP3 VERSION "
    "- GEMINI CANTONESE AUDIO VERIFY"
)

KEYWORDS = [
    "馬鼎盛",
    "马鼎盛",
]

# =========================================================
# 粵音驗證
#
# 依據：
# 馬 = maa5
# 鼎 = ding2
# 盛 = sing4 / sing6
#
# 原則：
# Gemini 如果沒有完整輸出「馬鼎盛」，
# 只要在相近位置依次命中三組中的兩組，
# 亦視為可能是「馬鼎盛」。
# =========================================================

MAA5_LIKE = [
    "馬", "马",
    "碼", "码",
    "瑪", "玛",
    "螞", "蚂",
]

DING2_LIKE = [
    "鼎",
    "頂", "顶",
    "酊",
]

SING4_LIKE = [
    "成",
    "城",
    "誠", "诚",
    "承",
    "乘",
    "繩", "绳",
    "盛",
]

SING6_LIKE = [
    "盛",
    "剩",
]

SING_LIKE = list(set(SING4_LIKE + SING6_LIKE))

# Gemini custom vocabulary。
# 這裡刻意以專名為主，不塞大量普通字。
GEMINI_CUSTOM_VOCABULARY = [
    "馬鼎盛",
    "講東講西",
    "香港電台第一台",
    "馬恩賜",
    "文潔華",
    "海林",
    "蘇奭",
    "蘇頴",
    "邱逸",
    "鄧達智",
    "黃仲遠",
]


PEOPLE_LABELS = [
    "主持人",
    "主持",
    "嘉賓",
    "嘉宾",
]


SUNDAY_GENERIC_HOSTS = [
    "馬鼎盛",
    "馬恩賜",
    "文潔華",
    "海林",
    "蘇奭",
    "蘇頴",
    "邱逸",
    "鄧達智",
    "黃仲遠",
]


PROGRAMS = {
    "sunday": {
        "programme_code": "free_as_the_wind_sunday",
        "home_url":
            "https://www.rthk.hk/radio/radio1/programme/"
            "free_as_the_wind_sunday",
        "episode_base":
            "https://www.rthk.hk/radio/radio1/programme/"
            "free_as_the_wind_sunday/episode/",
        "weekday": 6,
    },

    "monday": {
        "programme_code": "Free_as_the_wind",
        "home_url":
            "https://www.rthk.hk/radio/radio1/programme/"
            "Free_as_the_wind",
        "episode_base":
            "https://www.rthk.hk/radio/radio1/programme/"
            "Free_as_the_wind/episode/",
        "weekday": 0,
    },
}


CATCHUP_BASE = "https://www.rthk.hk/radio/catchUp"


# =========================================================
# HTTP
# =========================================================

def fetch(url, referer=None):
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept":
            "text/html,application/xhtml+xml,"
            "application/xml,application/json;q=0.9,*/*;q=0.8",
        "X-Requested-With": "XMLHttpRequest",
    }

    if referer:
        headers["Referer"] = referer

    response = requests.get(
        url,
        headers=headers,
        timeout=30,
    )

    response.raise_for_status()

    return response.text


# =========================================================
# 日期
# =========================================================

def parse_date(value):
    if not value:
        return None

    value = str(value).strip()

    patterns = [
        "%d/%m/%Y",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d-%m-%Y",
    ]

    for pattern in patterns:
        try:
            return datetime.strptime(
                value,
                pattern,
            ).date()

        except ValueError:
            pass

    return None


# =========================================================
# RTHK API
# =========================================================

def extract_episode_meta_from_raw(raw):
    meta = {}

    try:
        data = json.loads(raw)

        content = data.get(
            "content",
            [],
        )

        if isinstance(content, list):

            for item in content:

                episode_id = item.get("id")

                if not episode_id:
                    continue

                episode_id = str(
                    episode_id
                )

                meta[episode_id] = {
                    "api_title":
                        item.get(
                            "title",
                            "",
                        ),

                    "api_date":
                        item.get(
                            "date",
                            "",
                        ),
                }

    except Exception:
        pass

    return meta


def extract_episode_ids(raw):
    episode_ids = []

    try:
        data = json.loads(raw)

        content = data.get(
            "content",
            [],
        )

        if isinstance(content, list):

            for item in content:

                episode_id = item.get(
                    "id"
                )

                if episode_id:
                    episode_ids.append(
                        str(episode_id)
                    )

        if episode_ids:
            return episode_ids

    except Exception:
        pass

    episode_ids.extend(
        re.findall(
            r'data-episode=["\']?(\d+)',
            raw,
        )
    )

    episode_ids.extend(
        re.findall(
            r"/episode/(\d+)",
            raw,
        )
    )

    seen = set()
    unique_ids = []

    for episode_id in episode_ids:

        if episode_id not in seen:

            seen.add(
                episode_id
            )

            unique_ids.append(
                episode_id
            )

    return unique_ids


def fetch_home_episode_ids(program):

    raw = fetch(
        program["home_url"]
    )

    return extract_episode_ids(
        raw
    )


def fetch_catchup_episode_ids(
    program,
    max_pages,
):
    all_ids = []
    all_meta = {}

    for page in range(
        1,
        max_pages + 1,
    ):
        params = (
            f"?c=radio1"
            f"&p={program['programme_code']}"
            f"&page={page}"
            f"&m="
        )

        url = (
            CATCHUP_BASE
            + params
        )

        try:
            raw = fetch(
                url,
                referer=
                    program["home_url"],
            )

        except Exception as exc:

            print(
                f"ERROR fetching "
                f"catchUp page "
                f"{page}: {exc}"
            )

            continue

        ids = extract_episode_ids(
            raw
        )

        meta = (
            extract_episode_meta_from_raw(
                raw
            )
        )

        all_meta.update(
            meta
        )

        if not ids:
            break

        all_ids.extend(
            ids
        )

        time.sleep(
            0.3
        )

    seen = set()
    unique_ids = []

    for episode_id in all_ids:

        if episode_id not in seen:

            seen.add(
                episode_id
            )

            unique_ids.append(
                episode_id
            )

    return (
        unique_ids,
        all_meta,
    )


# =========================================================
# HTML
# =========================================================

def normalize_text(raw_html):

    soup = BeautifulSoup(
        raw_html,
        "html.parser",
    )

    for tag in soup(
        ["script", "style"]
    ):
        tag.decompose()

    text = soup.get_text(
        "\n"
    )

    text = html_lib.unescape(
        text
    )

    lines = []

    for line in text.splitlines():

        line = line.strip()

        if line:
            lines.append(
                line
            )

    return lines


def normalize_people_text(text):

    text = text or ""

    text = text.replace(
        " ",
        "",
    )

    text = text.replace(
        "　",
        "",
    )

    text = text.replace(
        ",",
        "、",
    )

    text = text.replace(
        "，",
        "、",
    )

    text = text.replace(
        "/",
        "、",
    )

    text = text.replace(
        "／",
        "、",
    )

    text = text.replace(
        "|",
        "、",
    )

    return text


def normalize_transcript(text):

    text = text or ""

    replacements = [
        " ",
        "　",
        ",",
        "，",
        "、",
        ".",
        "。",
        "：",
        ":",
        "；",
        ";",
        "！",
        "!",
        "？",
        "?",
        "「",
        "」",
        "『",
        "』",
        "（",
        "）",
        "(",
        ")",
        "\n",
        "\r",
        "\t",
    ]

    for item in replacements:

        text = text.replace(
            item,
            "",
        )

    return text


def get_people_label(line):

    for label in PEOPLE_LABELS:

        if label in line:
            return label

    return None


def clean_people_line(
    line,
    label,
):

    text = line.strip()

    if (
        "：" in text
        or ":" in text
    ):

        text = re.sub(
            rf"^.*?{label}"
            rf"\s*[：:]\s*",
            "",
            text,
        )

        return text.strip()

    return ""


def looks_like_stop_line(line):

    if not line:
        return True

    if get_people_label(
        line
    ):
        return True

    # 修正之前把「最新 / LATEST」
    # 當成人名的問題。
    if line in [
        "最新",
        "LATEST",
        "Latest",
    ]:
        return True

    if "播放" in line:
        return True

    if "Full" in line:
        return True

    if "Part" in line:
        return True

    if "第一部份" in line:
        return True

    if "第二部份" in line:
        return True

    if re.search(
        r"\d{1,2}/\d{1,2}/\d{4}",
        line,
    ):
        return True

    if re.search(
        r"\d{4}-\d{1,2}-\d{1,2}",
        line,
    ):
        return True

    if len(line) > 80:
        return True

    return False


def collect_multiline_people_text(
    lines,
    start_index,
    first_text,
):

    parts = []

    if first_text:
        parts.append(
            first_text
        )

    for next_index in range(
        start_index + 1,
        min(
            len(lines),
            start_index + 6,
        ),
    ):

        next_line = (
            lines[next_index]
            .strip()
        )

        if looks_like_stop_line(
            next_line
        ):
            break

        parts.append(
            next_line
        )

    return "、".join(
        parts
    ).strip(
        "、 "
    )


def extract_people_items(
    lines,
):
    items = []

    for index, line in enumerate(
        lines
    ):

        label = get_people_label(
            line
        )

        if not label:
            continue

        first_text = (
            clean_people_line(
                line,
                label,
            )
        )

        text = (
            collect_multiline_people_text(
                lines,
                index,
                first_text,
            )
        )

        text = text.strip()

        if not text:
            continue

        items.append({
            "label": label,
            "text": text,
            "index": index,
        })

    return items


def split_people_names(
    text,
):

    normalized = (
        normalize_people_text(
            text
        )
    )

    for sep in [
        "、",
        "，",
        ",",
        "/",
        "／",
        "|",
        "及",
        "和",
    ]:

        normalized = (
            normalized.replace(
                sep,
                "、",
            )
        )

    parts = [
        part.strip()
        for part
        in normalized.split("、")
        if part.strip()
    ]

    ignore = {
        "最新",
        "LATEST",
        "Latest",
    }

    seen = set()
    names = []

    for part in parts:

        if part in ignore:
            continue

        if part not in seen:

            seen.add(
                part
            )

            names.append(
                part
            )

    return names


def count_people_from_items(
    people_items,
):

    names = []

    for item in people_items:

        names.extend(
            split_people_names(
                item["text"]
            )
        )

    seen = set()
    unique_names = []

    for name in names:

        if name not in seen:

            seen.add(
                name
            )

            unique_names.append(
                name
            )

    return (
        len(unique_names),
        unique_names,
    )


def is_generic_sunday_people_text(
    text,
):

    normalized = (
        normalize_people_text(
            text
        )
    )

    count = 0

    for name in (
        SUNDAY_GENERIC_HOSTS
    ):

        if name in normalized:
            count += 1

    return count >= 5


def build_people_fields(
    programme_name,
    people_items,
):

    host_parts = []
    guest_parts = []
    match_parts = []

    generic_sunday_detected = False

    for item in people_items:

        label = item["label"]
        text = item["text"]

        if label in [
            "主持人",
            "主持",
        ]:

            host_parts.append(
                text
            )

        if label in [
            "嘉賓",
            "嘉宾",
        ]:

            guest_parts.append(
                text
            )

        if (
            programme_name
            == "sunday"
            and
            is_generic_sunday_people_text(
                text
            )
        ):

            generic_sunday_detected = True

            print(
                "SUNDAY GENERIC "
                "PEOPLE LINE DETECTED:",
                text,
            )

        match_parts.append(
            text
        )

    hosts = " | ".join(
        host_parts
    )

    guests = " | ".join(
        guest_parts
    )

    matched_text = " ".join(
        match_parts
    )

    (
        people_count,
        people_names,
    ) = count_people_from_items(
        people_items
    )

    indexes = [
        item["index"]
        for item
        in people_items
    ]

    if indexes:
        anchor_index = min(
            indexes
        )
    else:
        anchor_index = -1

    return {
        "hosts": hosts,
        "guests": guests,
        "matched_text":
            matched_text,
        "anchor_index":
            anchor_index,
        "generic_sunday_detected":
            generic_sunday_detected,
        "people_count":
            people_count,
        "people_names":
            "、".join(
                people_names
            ),
    }


# =========================================================
# Title / date
# =========================================================

def extract_title_near_anchor(
    lines,
    anchor_index,
):

    if anchor_index <= 0:
        return ""

    candidates = []

    start = max(
        0,
        anchor_index - 8,
    )

    end = anchor_index

    for line in lines[
        start:end
    ]:

        if get_people_label(
            line
        ):
            continue

        if "播放" in line:
            continue

        if re.search(
            r"\d{1,2}/"
            r"\d{1,2}/"
            r"\d{4}",
            line,
        ):
            continue

        if re.search(
            r"\d{4}-"
            r"\d{1,2}-"
            r"\d{1,2}",
            line,
        ):
            continue

        if len(line) > 80:
            continue

        candidates.append(
            line
        )

    if candidates:
        return candidates[-1]

    return ""


def extract_date_near_anchor(
    lines,
    anchor_index,
):

    if anchor_index >= 0:

        start = max(
            0,
            anchor_index - 15,
        )

        end = min(
            len(lines),
            anchor_index + 25,
        )

        search_lines = lines[
            start:end
        ]

    else:
        search_lines = lines

    for line in search_lines:

        match = re.search(
            r"(\d{1,2}/"
            r"\d{1,2}/"
            r"\d{4})",
            line,
        )

        if match:

            return parse_date(
                match.group(1)
            )

    for line in lines:

        match = re.search(
            r"(\d{4}-"
            r"\d{1,2}-"
            r"\d{1,2})",
            line,
        )

        if match:

            return parse_date(
                match.group(1)
            )

    return None


# =========================================================
# 篩選規則
# =========================================================

def decide_match(
    programme_name,
    matched_text,
    generic_sunday_detected,
    people_count,
):

    rthk_name_matched = any(
        keyword in matched_text
        for keyword in KEYWORDS
    )

    # 星期一：
    # 官方名單有馬鼎盛就下載。
    if programme_name == "monday":

        if rthk_name_matched:

            return (
                True,
                False,
                "direct_name_match",
            )

        return (
            False,
            False,
            "not_matched",
        )

    # 星期日：
    #
    # 情況一：
    # 固定總名單或 >= 5 人
    # → 先下載
    # → Gemini 音訊核實
    #
    # 情況二：
    # 正常少於 5 人
    # → 有馬鼎盛才下載
    # → 不需要音訊核實
    if programme_name == "sunday":

        suspicious_people_list = (
            generic_sunday_detected
            or people_count >= 5
        )

        if suspicious_people_list:

            return (
                True,
                True,
                "generic_or_many_people",
            )

        if rthk_name_matched:

            return (
                True,
                False,
                "direct_name_match",
            )

        return (
            False,
            False,
            "not_matched",
        )

    return (
        False,
        False,
        "not_matched",
    )


def extract_detail(
    programme_name,
    episode_url,
):

    raw = fetch(
        episode_url
    )

    lines = normalize_text(
        raw
    )

    people_items = (
        extract_people_items(
            lines
        )
    )

    people_fields = (
        build_people_fields(
            programme_name,
            people_items,
        )
    )

    title = (
        extract_title_near_anchor(
            lines,
            people_fields[
                "anchor_index"
            ],
        )
    )

    episode_date = (
        extract_date_near_anchor(
            lines,
            people_fields[
                "anchor_index"
            ],
        )
    )

    (
        matched,
        needs_audio_verify,
        candidate_reason,
    ) = decide_match(
        programme_name=
            programme_name,

        matched_text=
            people_fields[
                "matched_text"
            ],

        generic_sunday_detected=
            people_fields[
                "generic_sunday_detected"
            ],

        people_count=
            people_fields[
                "people_count"
            ],
    )

    return {
        "date":
            episode_date.isoformat()
            if episode_date
            else "",

        "programme":
            programme_name,

        "title":
            title,

        "hosts":
            people_fields["hosts"],

        "guests":
            people_fields["guests"],

        "people_count":
            people_fields[
                "people_count"
            ],

        "people_names":
            people_fields[
                "people_names"
            ],

        "matched":
            matched,

        "candidate_reason":
            candidate_reason,

        "needs_audio_verify":
            needs_audio_verify,

        "generic_sunday_detected":
            people_fields[
                "generic_sunday_detected"
            ],

        "episode_url":
            episode_url,

        "filename":
            "",

        "download_status":
            "",

        "audio_verified":
            "",

        "audio_transcript":
            "",

        "audio_match_groups":
            "",

        "error":
            "",
    }


# =========================================================
# MP3
# =========================================================

def safe_filename_from_date(
    date_string,
):

    parsed = parse_date(
        date_string
    )

    if not parsed:

        try:
            parsed = datetime.strptime(
                date_string,
                "%Y-%m-%d",
            ).date()

        except ValueError:
            return "unknown"

    return parsed.strftime(
        "%m%d"
    )


def download_mp3(
    row,
    download_dir,
):

    episode_url = (
        row["episode_url"]
    )

    filename_base = (
        safe_filename_from_date(
            row["date"]
        )
    )

    Path(
        download_dir
    ).mkdir(
        parents=True,
        exist_ok=True,
    )

    output_template = str(
        Path(download_dir)
        / f"{filename_base}.%(ext)s"
    )

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

    print(
        "DOWNLOADING:",
        episode_url,
    )

    print(
        "OUTPUT:",
        output_template,
    )

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    if result.returncode != 0:

        print(
            result.stdout
        )

        raise RuntimeError(
            "yt-dlp failed "
            f"with exit code "
            f"{result.returncode}"
        )

    final_path = (
        Path(download_dir)
        / f"{filename_base}.mp3"
    )

    if not final_path.exists():

        candidates = list(
            Path(download_dir)
            .glob(
                f"{filename_base}.*"
            )
        )

        if candidates:
            final_path = candidates[0]

    return str(
        final_path
    )


# =========================================================
# 音訊截取
# =========================================================

def make_verify_clip(
    mp3_path,
    verify_dir,
    seconds,
):

    Path(
        verify_dir
    ).mkdir(
        parents=True,
        exist_ok=True,
    )

    source = Path(
        mp3_path
    )

    clip_path = (
        Path(verify_dir)
        / (
            f"{source.stem}"
            f"_first"
            f"{seconds}s.mp3"
        )
    )

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-t",
        str(seconds),
        "-vn",
        "-acodec",
        "libmp3lame",
        str(clip_path),
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    if result.returncode != 0:

        print(
            result.stdout
        )

        raise RuntimeError(
            "ffmpeg clip failed "
            f"with exit code "
            f"{result.returncode}"
        )

    return str(
        clip_path
    )


# =========================================================
# Gemini 粵語轉錄
# =========================================================

def transcribe_cantonese_audio_gemini(
    audio_path,
    episode_title="",
):

    from google import genai

    api_key = os.environ.get(
        "GEMINI_API_KEY"
    )

    if not api_key:

        raise RuntimeError(
            "GEMINI_API_KEY "
            "is not configured"
        )

    client = genai.Client(
        api_key=api_key
    )

    vocabulary = list(
        GEMINI_CUSTOM_VOCABULARY
    )

    # 如果成功取得當集題目，
    # 一併交給 Gemini 作 vocabulary hint。
    if (
        episode_title
        and episode_title.strip()
    ):

        vocabulary.append(
            episode_title.strip()
        )

    # 去除重複
    vocabulary = list(
        dict.fromkeys(
            vocabulary
        )
    )

    uploaded_file = None

    try:
        print(
            "UPLOADING VERIFY CLIP "
            "TO GEMINI:",
            audio_path,
        )

        uploaded_file = (
            client.files.upload(
                file=audio_path
            )
        )

        interaction = (
            client.interactions.create(
                model=
                    "gemini-3.5-transcribe",

                input=[
                    {
                        "type":
                            "audio",

                        "uri":
                            uploaded_file.uri,

                        "mime_type":
                            uploaded_file.mime_type,
                    }
                ],

                generation_config={
                    "transcription_config": {
                        "language_codes": [
                            "yue-Hant-HK"
                        ],

                        "custom_vocabulary":
                            vocabulary,

                        # 要核實原話，
                        # 不需要 Gemini 幫我們
                        # 改寫成漂亮文章。
                        "mode":
                            "verbatim",
                    }
                },
            )
        )

        transcript = (
            interaction.output_text
            or ""
        ).strip()

        print(
            "GEMINI CANTONESE "
            "TRANSCRIPT:",
            transcript,
        )

        return transcript

    finally:

        # 上傳到 Gemini Files API
        # 的臨時檔案用完即刪。
        if uploaded_file is not None:

            try:
                client.files.delete(
                    name=
                        uploaded_file.name
                )

                print(
                    "GEMINI TEMP FILE "
                    "DELETED"
                )

            except Exception as exc:

                print(
                    "WARNING: could not "
                    "delete Gemini temp "
                    f"file: {exc}"
                )


# =========================================================
# 粵音二組以上核實
# =========================================================

def get_sound_group(
    char,
):

    if char in MAA5_LIKE:
        return "maa5"

    if char in DING2_LIKE:
        return "ding2"

    if char in SING_LIKE:
        return "sing4_or_sing6"

    return None


def find_cantonese_name_pair(
    normalized,
    max_distance=6,
):
    """
    找出 maa5 → ding2 → sing4/sing6
    三組中的任何兩組，而且必須：
      1. 順序合理
      2. 相距不遠

    避免以前「整段 120 秒任何地方
    各出現一個字」就誤判。
    """

    positions = {
        "maa5": [],
        "ding2": [],
        "sing4_or_sing6": [],
    }

    for index, char in enumerate(
        normalized
    ):

        group = get_sound_group(
            char
        )

        if group:
            positions[group].append(
                index
            )

    ordered_pairs = [
        (
            "maa5",
            "ding2",
        ),
        (
            "maa5",
            "sing4_or_sing6",
        ),
        (
            "ding2",
            "sing4_or_sing6",
        ),
    ]

    for (
        first_group,
        second_group,
    ) in ordered_pairs:

        for first_pos in (
            positions[first_group]
        ):

            for second_pos in (
                positions[second_group]
            ):

                distance = (
                    second_pos
                    - first_pos
                )

                if (
                    1
                    <= distance
                    <= max_distance
                ):

                    return (
                        True,
                        (
                            f"{first_group}"
                            f"+"
                            f"{second_group}"
                        ),
                    )

    return (
        False,
        "",
    )


def audio_mentions_target(
    transcript,
):

    normalized = (
        normalize_transcript(
            transcript
        )
    )

    # 第一級：
    # 完整名稱
    for keyword in KEYWORDS:

        if keyword in normalized:

            return (
                True,
                "exact_name",
            )

    # 第二級：
    # CUHK 粵音三組中，
    # 有兩組按合理順序且相近。
    (
        matched,
        groups,
    ) = find_cantonese_name_pair(
        normalized,
        max_distance=6,
    )

    if matched:

        return (
            True,
            groups,
        )

    return (
        False,
        "",
    )


# =========================================================
# Error row
# =========================================================

def empty_error_row(
    programme_name,
    episode_url,
    error,
):

    return {
        "date": "",
        "programme":
            programme_name,
        "title": "",
        "hosts": "",
        "guests": "",
        "people_count": "",
        "people_names": "",
        "matched": False,
        "candidate_reason": "",
        "needs_audio_verify": "",
        "generic_sunday_detected":
            "",
        "filename": "",
        "episode_url":
            episode_url,
        "download_status": "",
        "audio_verified": "",
        "audio_transcript": "",
        "audio_match_groups": "",
        "error":
            error,
    }


# =========================================================
# Process programme
# =========================================================

def process_program(
    programme_name,
    program,
    start_date,
    end_date,
    max_pages,
):

    rows = []
    errors = []

    episode_ids = []
    episode_meta = {}

    try:

        episode_ids.extend(
            fetch_home_episode_ids(
                program
            )
        )

    except Exception as exc:

        errors.append(
            empty_error_row(
                programme_name,
                program["home_url"],
                (
                    "home fetch failed: "
                    f"{exc}"
                ),
            )
        )

    try:

        (
            catchup_ids,
            catchup_meta,
        ) = fetch_catchup_episode_ids(
            program,
            max_pages,
        )

        episode_ids.extend(
            catchup_ids
        )

        episode_meta.update(
            catchup_meta
        )

    except Exception as exc:

        errors.append(
            empty_error_row(
                programme_name,
                program["home_url"],
                (
                    "catchUp fetch "
                    f"failed: {exc}"
                ),
            )
        )

    seen = set()
    unique_episode_ids = []

    for episode_id in episode_ids:

        if episode_id not in seen:

            seen.add(
                episode_id
            )

            unique_episode_ids.append(
                episode_id
            )

    print(
        f"{programme_name}: "
        f"found "
        f"{len(unique_episode_ids)} "
        f"unique episode IDs"
    )

    for episode_id in (
        unique_episode_ids
    ):

        episode_url = (
            program["episode_base"]
            + str(episode_id)
        )

        try:

            row = extract_detail(
                programme_name,
                episode_url,
            )

            meta = episode_meta.get(
                str(episode_id),
                {},
            )

            api_date = parse_date(
                meta.get(
                    "api_date",
                    "",
                )
            )

            api_title = meta.get(
                "api_title",
                "",
            )

            if (
                not row["date"]
                and api_date
            ):

                row["date"] = (
                    api_date.isoformat()
                )

                print(
                    "DATE FALLBACK "
                    "FROM API:",
                    episode_url,
                    row["date"],
                )

            if (
                not row["title"]
                and api_title
            ):

                row["title"] = (
                    api_title
                )

            if not row["date"]:

                print(
                    "SKIP NO DATE:",
                    episode_url,
                    meta,
                )

                continue

            episode_date = (
                datetime.strptime(
                    row["date"],
                    "%Y-%m-%d",
                ).date()
            )

            if (
                episode_date
                < start_date
                or episode_date
                > end_date
            ):
                continue

            if (
                episode_date.weekday()
                != program["weekday"]
            ):
                continue

            row["filename"] = (
                safe_filename_from_date(
                    row["date"]
                )
                + ".mp3"
            )

            rows.append(
                row
            )

        except Exception as exc:

            errors.append(
                empty_error_row(
                    programme_name,
                    episode_url,
                    str(exc),
                )
            )

        time.sleep(
            0.3
        )

    return (
        rows,
        errors,
    )


# =========================================================
# CSV
# =========================================================

def write_csv(
    path,
    rows,
):

    fieldnames = [
        "date",
        "programme",
        "title",
        "hosts",
        "guests",
        "people_count",
        "people_names",
        "matched",
        "candidate_reason",
        "needs_audio_verify",
        "generic_sunday_detected",
        "filename",
        "episode_url",
        "download_status",
        "audio_verified",
        "audio_transcript",
        "audio_match_groups",
        "error",
    ]

    Path(
        path
    ).parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with open(
        path,
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )

        writer.writeheader()

        for row in rows:

            clean_row = {
                field:
                    row.get(
                        field,
                        "",
                    )
                for field
                in fieldnames
            }

            writer.writerow(
                clean_row
            )


# =========================================================
# Main
# =========================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--start",
        required=True,
        help=
            "Start date, YYYY-MM-DD",
    )

    parser.add_argument(
        "--end",
        default=
            date.today().isoformat(),
        help=
            "End date, YYYY-MM-DD",
    )

    parser.add_argument(
        "--max-pages",
        type=int,
        default=20,
    )

    parser.add_argument(
        "--download",
        action="store_true",
    )

    parser.add_argument(
        "--download-dir",
        default="downloads",
    )

    parser.add_argument(
        "--verify-sunday-audio",
        action="store_true",
    )

    parser.add_argument(
        "--verify-seconds",
        type=int,
        default=120,
    )

    parser.add_argument(
        "--verify-dir",
        default="verify_clips",
    )

    args = parser.parse_args()

    start_date = (
        datetime.strptime(
            args.start,
            "%Y-%m-%d",
        ).date()
    )

    end_date = (
        datetime.strptime(
            args.end,
            "%Y-%m-%d",
        ).date()
    )

    all_rows = []
    all_errors = []

    for (
        programme_name,
        program,
    ) in PROGRAMS.items():

        (
            rows,
            errors,
        ) = process_program(
            programme_name=
                programme_name,

            program=
                program,

            start_date=
                start_date,

            end_date=
                end_date,

            max_pages=
                args.max_pages,
        )

        all_rows.extend(
            rows
        )

        all_errors.extend(
            errors
        )

    matched_rows = [
        row
        for row
        in all_rows
        if row.get(
            "matched"
        ) is True
    ]

    if args.download:

        for row in matched_rows:

            downloaded_file = None

            try:

                downloaded_path = (
                    download_mp3(
                        row,
                        args.download_dir,
                    )
                )

                downloaded_file = Path(
                    downloaded_path
                )

                row["filename"] = (
                    downloaded_file.name
                )

                row["download_status"] = (
                    "downloaded"
                )

                # =========================
                # 星期日可疑名單：
                # Gemini 核實
                # =========================

                if (
                    args.verify_sunday_audio
                    and
                    row.get(
                        "programme"
                    ) == "sunday"
                    and
                    str(
                        row.get(
                            "needs_audio_verify"
                        )
                    ).lower()
                    == "true"
                ):

                    print(
                        "VERIFYING SUNDAY "
                        "AUDIO WITH GEMINI:",
                        downloaded_path,
                    )

                    clip_path = (
                        make_verify_clip(
                            mp3_path=
                                downloaded_path,

                            verify_dir=
                                args.verify_dir,

                            seconds=
                                args.verify_seconds,
                        )
                    )

                    transcript = (
                        transcribe_cantonese_audio_gemini(
                            audio_path=
                                clip_path,

                            episode_title=
                                row.get(
                                    "title",
                                    "",
                                ),
                        )
                    )

                    row[
                        "audio_transcript"
                    ] = transcript

                    (
                        audio_verified,
                        audio_match_groups,
                    ) = (
                        audio_mentions_target(
                            transcript
                        )
                    )

                    row[
                        "audio_match_groups"
                    ] = (
                        audio_match_groups
                    )

                    if audio_verified:

                        row[
                            "audio_verified"
                        ] = "true"

                        row[
                            "download_status"
                        ] = (
                            "downloaded_"
                            "gemini_verified"
                        )

                        print(
                            "GEMINI AUDIO "
                            "VERIFIED: "
                            "keep file"
                        )

                    else:

                        row[
                            "audio_verified"
                        ] = "false"

                        row[
                            "download_status"
                        ] = (
                            "rejected_by_"
                            "gemini_audio_check"
                        )

                        if (
                            downloaded_file
                            .exists()
                        ):

                            downloaded_file.unlink()

                        print(
                            "GEMINI AUDIO "
                            "NOT VERIFIED: "
                            "deleted file"
                        )

                elif (
                    row.get(
                        "programme"
                    )
                    == "sunday"
                ):

                    row[
                        "audio_verified"
                    ] = "not_required"

            except Exception as exc:

                row[
                    "download_status"
                ] = "failed"

                row[
                    "error"
                ] = str(exc)

                # Gemini / API / verification
                # 技術失敗時：
                # 不讓未核實檔案進入 rclone。
                if (
                    row.get(
                        "programme"
                    )
                    == "sunday"
                    and
                    str(
                        row.get(
                            "needs_audio_verify"
                        )
                    ).lower()
                    == "true"
                ):

                    if (
                        downloaded_file
                        is not None
                        and
                        downloaded_file.exists()
                    ):

                        downloaded_file.unlink()

                        print(
                            "VERIFY ERROR: "
                            "unverified Sunday "
                            "file deleted"
                        )

                all_errors.append(
                    row.copy()
                )

    write_csv(
        "output/all_episodes.csv",
        all_rows,
    )

    write_csv(
        "output/matched_episodes.csv",
        matched_rows,
    )

    write_csv(
        "output/errors.csv",
        all_errors,
    )

    print(
        "DONE"
    )

    print(
        f"all rows: "
        f"{len(all_rows)}"
    )

    print(
        f"matched rows: "
        f"{len(matched_rows)}"
    )

    print(
        f"errors: "
        f"{len(all_errors)}"
    )


if __name__ == "__main__":
    main()

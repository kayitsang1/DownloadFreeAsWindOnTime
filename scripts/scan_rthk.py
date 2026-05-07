import requests
from bs4 import BeautifulSoup

TEST_URL = "https://www.rthk.hk/radio/radio1/programme/free_as_the_wind_sunday/episode/1096310"

print("RUNNING SMOKE TEST V3")

headers = {
    "User-Agent": "Mozilla/5.0"
}

r = requests.get(TEST_URL, headers=headers, timeout=30)

print("status:", r.status_code)
print("length:", len(r.text))
print("contains 馬鼎盛:", "馬鼎盛" in r.text)

soup = BeautifulSoup(r.text, "html.parser")
text = soup.get_text("\n")

print("text contains 馬鼎盛:", "馬鼎盛" in text)

for line in text.splitlines():
    line = line.strip()
    if "主持" in line or "馬鼎盛" in line:
        print("MATCH LINE:", line)

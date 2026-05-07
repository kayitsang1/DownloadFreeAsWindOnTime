import csv
import os
import re
import requests
from bs4 import BeautifulSoup

KEYWORD = "馬鼎盛"

def fetch(url):
    return requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30).text

def extract(url):
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")

    match = re.search(r"(主持人?|主持)\s*[:：]\s*(.*)", text)
    hosts = match.group(2).strip() if match else ""

    return {
        "url": url,
        "hosts": hosts,
        "matched": KEYWORD in hosts
    }

os.makedirs("output", exist_ok=True)

rows = []

for url in TEST_EPISODES:
    row = extract(url)
    print(row)
    rows.appe

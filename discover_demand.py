#!/usr/bin/env python3
"""Discover Osaka demand drivers from public news RSS; candidates never imply verified dates.
Confirmed items are a small explicit registry of organizer/municipal verified events.
Standard library only. Output consumed by v7.18 HTML.
"""
import datetime as dt
import hashlib
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / 'data' / 'discovered-events.json'
QUERIES = [
    '大阪 花火大会 祭り 開催',
    '大阪 大規模 イベント 開催 2026 2027',
    '大阪 全国大会 学会 国際会議 開催',
    '大阪 スポーツ 大会 観客 開催',
    '大阪 交通規制 大規模 催し',
]
KEYWORDS = re.compile(r'花火|祭り|まつり|フェス|全国大会|国際会議|学会|万博|マラソン|パレード|大型イベント|交通規制|ライブ|コンサート|博覧会|展示会')
OSAKA = re.compile(r'大阪|淀川|十三|梅田|吹田|豊中|堺市|枚方|高槻|東大阪|万博記念公園|京セラドーム|インテックス')
DATE = re.compile(r'(?:(20\d{2})\s*[年/.-]\s*)?(1[0-2]|0?[1-9])\s*[月/.-]\s*(3[01]|[12]\d|0?[1-9])\s*日?')

# Confirmed against the organizer's official announcement and Osaka City designated-event list.
# An explicit registry is deliberately NOT called automated verification.
CONFIRMED = [
    {
        'id': 'discovery-confirmed-yodogawa-2026',
        'title': '第38回 なにわ淀川花火大会',
        'start': '2026-10-17', 'end': '2026-10-17', 'time': '19:00',
        'category': 'その他', 'venue': '淀川河川敷（十三会場）',
        'url': 'https://www.yodohanabi.com/3118.html',
        'source': 'なにわ淀川花火大会 主催者公式発表',
        'notes': '公式開催日確認済み。来場者数・新大阪宿泊率は未確認。定性的な確認優先度のみ加点。',
        'attendance': None, 'attendanceBasis': 'unknown',
        'discoveryImpact': 12, 'discoveryConfidence': 'official',
        'verified': True, 'autoCollected': True,
    },
]

def fetch_rss(query):
    url = 'https://news.google.com/rss/search?' + urllib.parse.urlencode({
        'q': query + ' when:180d', 'hl': 'ja', 'gl': 'JP', 'ceid': 'JP:ja'})
    req = urllib.request.Request(url, headers={'User-Agent': 'ShinOsakaEventNavi/7.19 (+public RSS research; once per day)'})
    with urllib.request.urlopen(req, timeout=15) as res:
        payload = res.read(1_500_000)
    root = ET.fromstring(payload)
    out = []
    for item in root.findall('.//channel/item')[:35]:
        title = (item.findtext('title') or '').strip()
        desc = re.sub(r'<[^>]*>', ' ', item.findtext('description') or '')
        link = (item.findtext('link') or '').strip()
        pub = (item.findtext('pubDate') or '').strip()
        if not link.startswith('https://') or not KEYWORDS.search(title) or not OSAKA.search(title + ' ' + desc):
            continue
        # No inferred dates from publication date; only explicit dates in the headline.
        found = DATE.search(title)
        candidate_date = None
        if found:
            today = dt.date.today()
            year = int(found.group(1) or today.year)
            try:
                event_day = dt.date(year, int(found.group(2)), int(found.group(3)))
                # When a headline omits the year, allow the next calendar year.
                if not found.group(1) and event_day < today - dt.timedelta(days=14):
                    event_day = dt.date(year + 1, event_day.month, event_day.day)
                candidate_date = event_day.isoformat()
            except ValueError:
                pass
        uid = hashlib.sha256((title + '|' + link).encode()).hexdigest()[:16]
        out.append({'id': 'candidate-' + uid, 'title': title[:180], 'dateHint': candidate_date,
                    'url': link, 'source': 'GoogleニュースRSS（未確認）', 'published': pub,
                    'confidence': 'candidate', 'reason': '大阪関連・大型集客キーワードに該当。開催日・規模は要公式確認。'})
    return out

def run():
    candidates, errors = {}, []
    for q in QUERIES:
        try:
            for e in fetch_rss(q):
                # Same title across searches is one discovery, not many demand drivers.
                key = re.sub(r'\s+', '', e['title']).casefold()
                candidates.setdefault(key, e)
        except Exception as ex:
            errors.append(f'{q}: {type(ex).__name__}: {str(ex)[:100]}')
    today = dt.date.today()
    limit = today + dt.timedelta(days=184)
    if len(errors) == len(QUERIES):
        # Avoid overwriting a previously good discovery dataset with empty results.
        raise RuntimeError('All news RSS searches failed: ' + '; '.join(errors))
    confirmed = [x for x in CONFIRMED if today <= dt.date.fromisoformat(x['start']) <= limit]
    # A dated candidate must fall inside six months; undated items are still shown for review.
    selected = [x for x in candidates.values() if not x['dateHint'] or today <= dt.date.fromisoformat(x['dateHint']) <= limit]
    selected.sort(key=lambda x: (x['dateHint'] is None, x['dateHint'] or '', x['title']))
    payload = {'version': '7.19', 'checkedAt': dt.datetime.now(dt.timezone.utc).isoformat(),
               'confirmed': confirmed, 'candidates': selected[:100], 'queries': len(QUERIES),
               'successfulQueries': len(QUERIES) - len(errors), 'errors': errors,
               'note': '未確認候補は総合需要に反映しません。公式確認済みでも定性的加点は実績に基づく予測ではありません。'}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'confirmed={len(confirmed)} candidates={len(selected)} errors={len(errors)}')
    return 0

if __name__ == '__main__':
    sys.exit(run())

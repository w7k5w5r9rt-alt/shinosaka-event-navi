#!/usr/bin/env python3
"""Discover Osaka demand drivers from public news RSS; candidates never imply verified dates.
Confirmed items are a small explicit registry of organizer/municipal verified events.
Standard library only. Output consumed by v7.18 HTML.
"""
import datetime as dt
from zoneinfo import ZoneInfo
import email.utils
import unicodedata
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
# Exclude article formats that are not future event announcements.
EXCLUDE = re.compile(r'まとめ|一覧|ランキング|選|振り返り|レポート|開催レポ|大盛況|御礼|終了|閉幕|昨年|去年|過去|写真\s*[|｜]|画像\s*[|｜]|とは[？?]|結果発表|開催された|来場者数.*集結|交通規制のみ|リニューアル工事|工事のお知らせ|東京で開催|開催地未定')
EVENT_WORDS = re.compile(r'開催|開幕|決定|発表|日程|実施|予定|開演|打ち上げ|募集|チケット|出展')
# Headline dates are clues, not official verification.
DATE = re.compile(r'(?<!\d)(?:(20\d{2})\s*[年/.-]\s*)?(1[0-2]|0?[1-9])\s*[月/.-]\s*(3[01]|[12]\d|0?[1-9])\s*日?(?!\d)')
PAST_CONTEXT = re.compile(r'本日開催|本日打ち上げ|昨日|先日|開催した|開催されました|開催され|開催終了|閉幕|終演|振り返|開催レポ|実施した|行われた|盛況|終了しました')
JST = ZoneInfo('Asia/Tokyo')

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

def normalized_title(title):
    title = unicodedata.normalize('NFKC', title).lower()
    # Publisher suffixes and formatting differences must not generate duplicate cards.
    title = re.sub(r'\s+[-–—|｜]\s+[^-–—|｜]{2,50}$', '', title)
    return re.sub(r'[^0-9a-z一-龥ぁ-んァ-ン]', '', title)


def published_date(value):
    try:
        return email.utils.parsedate_to_datetime(value).date()
    except (TypeError, ValueError, IndexError):
        return None


def headline_date(title, today, published=None):
    """Return a future date only when year is explicit or publication context is safe.

    Never roll an already-passed yearless date into the next year.  If the
    headline mentions a year elsewhere, use it only when unambiguous.
    """
    m = DATE.search(title)
    if not m:
        return None
    explicit_year = int(m.group(1)) if m.group(1) else None
    years = {int(y) for y in re.findall(r'(?<!\d)(20\d{2})\s*年?(?!\d)', title)}
    if explicit_year:
        year = explicit_year
    elif len(years) == 1:
        year = next(iter(years))
    elif years:
        return None
    else:
        # Yearless headlines can be treated as current-year announcements only
        # if recently published. No automatic next-year rollover.
        if published is None or not (today - dt.timedelta(days=30) <= published <= today + dt.timedelta(days=1)):
            return None
        year = today.year
    try:
        day = dt.date(year, int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    if not (today <= day <= today + dt.timedelta(days=184)):
        return None
    return day


def assess_headline(title, desc, pub, today):
    """Return conservative ranking and date hint; never imply official confirmation."""
    title = unicodedata.normalize('NFKC', title).strip()
    if not KEYWORDS.search(title) or not OSAKA.search(title + ' ' + desc):
        return None
    if EXCLUDE.search(title) or PAST_CONTEXT.search(title):
        return None
    if not EVENT_WORDS.search(title):
        return None
    pdate = published_date(pub)
    if pdate and (pdate > today + dt.timedelta(days=1) or pdate < today - dt.timedelta(days=90)):
        return None
    day = headline_date(title, today, pdate)
    # Strict future-only mode: do not publish undated news as event candidates.
    if day is None:
        return None
    # Undated news is useful only as a secondary lead, not an actionable event.
    score = 0
    if day: score += 45
    if re.search(r'淀川|十三|梅田|新大阪|大阪市|吹田|豊中|インテックス|京セラドーム', title): score += 18
    elif re.search(r'大阪|枚方|高槻|堺市|万博記念公園', title): score += 12
    if re.search(r'全国大会|国際会議|学会|マラソン|花火大会|博覧会|展示会|コンサート|ライブ|大規模', title): score += 20
    elif re.search(r'花火|祭り|まつり|フェス|パレード', title): score += 12
    if re.search(r'開催決定|開催日|開催予定|日程発表|チケット発売|出展募集', title): score += 12
    if score < 20:
        return None
    reason = '未来の開催日候補あり・公式照合が必要'
    return score, day.isoformat() if day else None, reason


def fetch_rss(query):
    url = 'https://news.google.com/rss/search?' + urllib.parse.urlencode({
        'q': query + ' when:90d', 'hl': 'ja', 'gl': 'JP', 'ceid': 'JP:ja'})
    req = urllib.request.Request(url, headers={'User-Agent': 'ShinOsakaEventNavi/7.20 (public RSS; scheduled)'})
    with urllib.request.urlopen(req, timeout=15) as res:
        payload = res.read(1_500_000)
    root = ET.fromstring(payload)
    out = []
    today = dt.datetime.now(JST).date()
    for item in root.findall('.//channel/item')[:50]:
        title = (item.findtext('title') or '').strip()
        desc = re.sub(r'<[^>]*>', ' ', item.findtext('description') or '')
        link = (item.findtext('link') or '').strip()
        pub = (item.findtext('pubDate') or '').strip()
        if not link.startswith('https://'):
            continue
        assessment = assess_headline(title, desc, pub, today)
        if not assessment:
            continue
        score, candidate_date, reason = assessment
        key = normalized_title(title)
        uid = hashlib.sha256(key.encode()).hexdigest()[:16]
        out.append({'id': 'candidate-' + uid, 'title': title[:180], 'dateHint': candidate_date,
                    'url': link, 'source': 'GoogleニュースRSS（未確認）', 'published': pub,
                    'confidence': 'candidate', 'relevance': score, 'reason': reason})
    return out

def run():
    candidates, errors = {}, []
    for q in QUERIES:
        try:
            for e in fetch_rss(q):
                # Same title across searches is one discovery, not many demand drivers.
                key = normalized_title(e['title'])
                old = candidates.get(key)
                if old is None or e['relevance'] > old['relevance']:
                    candidates[key] = e
        except Exception as ex:
            errors.append(f'{q}: {type(ex).__name__}: {str(ex)[:100]}')
    today = dt.datetime.now(JST).date()
    limit = today + dt.timedelta(days=184)
    if len(errors) == len(QUERIES):
        # Avoid overwriting a previously good discovery dataset with empty results.
        raise RuntimeError('All news RSS searches failed: ' + '; '.join(errors))
    confirmed = [x for x in CONFIRMED if today <= dt.date.fromisoformat(x['start']) <= limit]
    # Every candidate must have a future date within the rolling window.
    selected = [x for x in candidates.values() if x['dateHint'] and today <= dt.date.fromisoformat(x['dateHint']) <= limit]
    # Keep dated, higher-confidence leads first. Avoid flooding the iPhone with vague news.
    selected.sort(key=lambda x: (x['dateHint'] is None, -x['relevance'], x['dateHint'] or '', x['title']))
    selected = selected[:25]
    payload = {'version': '7.20-future-only', 'checkedAt': dt.datetime.now(dt.timezone.utc).isoformat(),
               'confirmed': confirmed, 'candidates': selected, 'queries': len(QUERIES),
               'successfulQueries': len(QUERIES) - len(errors), 'errors': errors, 'selectionPolicy': '未来の開催日が読み取れる候補のみ表示。過去・日付不明・開催年が曖昧な記事を除外。未確認はスコア加算なし。',
               'note': '未確認候補は総合需要に反映しません。公式確認済みでも定性的加点は実績に基づく予測ではありません。'}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'confirmed={len(confirmed)} candidates={len(selected)} errors={len(errors)}')
    return 0

if __name__ == '__main__':
    sys.exit(run())

"""Shinosaka Event Navi: official schedules, conservative beta collector.
Requires requests and beautifulsoup4. Run: python collector.py
"""
import calendar
import hashlib
import json
import os
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, 'data')
os.makedirs(DATA, exist_ok=True)
NOW = datetime.now(timezone.utc).isoformat()
JST = timezone(timedelta(hours=9))
TODAY = datetime.now(JST).date()
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; ShinosakaEventNavi/1.1; event information verification)'}
DATE = re.compile(r'(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日')


def read_json(name, default):
    try:
        with open(os.path.join(DATA if name.startswith('data/') else ROOT, name[5:] if name.startswith('data/') else name), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(name, obj):
    with open(os.path.join(DATA, name), 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def months(n=6):
    y, m = TODAY.year, TODAY.month
    for offset in range(n):
        yy = y + (m - 1 + offset) // 12
        mm = (m - 1 + offset) % 12 + 1
        yield yy, mm


def normalize(s):
    return re.sub(r'\s+', ' ', str(s)).strip()


def event_id(venue, title, day):
    # Day retained to distinguish two performances of the same tour.
    # A date move is flagged as a possible new/disappeared event, not automatically a confirmed cancellation.
    raw = '|'.join((venue, normalize(title).casefold(), day))
    return 'auto-' + hashlib.sha256(raw.encode()).hexdigest()[:16]


def make_event(src, title, day, url, category='その他', time='', notes=''):
    title = normalize(title)
    if not title or title == '非公開イベント':
        return None
    venue = src.get('venue', src['name'])
    return {'id': event_id(venue, title, day), 'title': title,
            'start': day, 'end': day, 'time': time, 'category': category,
            'venue': venue, 'scale': 3, 'access': 3, 'url': url,
            'notes': '公式サイトの公開情報から自動抽出。日時・変更・中止は公式サイトで再確認。' + (' ' + notes if notes else ''),
            'source': src['name'], 'verified': False, 'autoCollected': True,
            'status': 'scheduled', 'checkedAt': NOW}


def kyocera(html, src, url):
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'noscript', 'svg']): tag.decompose()
    text = normalize(soup.get_text(' ', strip=True))
    # A published event block has: date, performer/title, category, 日時, date.
    # Avoid interpreting the calendar's date-only navigation as an event.
    date_pat = r'(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日(?:[（(][^）)]{1,4}[）)])?'
    cat_pat = r'(コンサート|野球|エンターテインメント|販売・展示・その他|その他のスポーツ)'
    pat = re.compile(date_pat + r'\s+(.{2,260}?)\s+' + cat_pat + r'\s+日時\s+' + date_pat)
    result = []
    for m in pat.finditer(text):
        y, mo, d = map(int, m.group(1, 2, 3))
        if (y, mo, d) != tuple(map(int, m.group(6, 7, 8))): continue
        title = normalize(m.group(4))
        if title == '非公開イベント' or 'イベント詳細' in title or len(title) > 180: continue
        day = f'{y:04d}-{mo:02d}-{d:02d}'
        following = text[m.end():m.end()+180]
        tm = re.search(r'(?:開始時間|開演)\s*[:：]?\s*(\d{1,2}:\d{2})', following)
        ev = make_event(src, title, day, url, m.group(5), tm.group(1) if tm else '')
        if ev: result.append(ev)
    return list({e['id']:e for e in result}.values())


def johall(html, src, url):
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'noscript', 'svg']): tag.decompose()
    text = normalize(soup.get_text(' ', strip=True))
    # Month pages contain cards: YYYY/MM/DD genre title 開場 HH:MM 開演 HH:MM.
    pat = re.compile(r'(20\d{2})/(\d{1,2})/(\d{1,2})\s+(音楽・芸能|集会・式典|スポーツ|展示・販売|その他|イベント)\s+(.{2,180}?)\s+(?:開場|開演|開始)\s+')
    found=[]
    for m in pat.finditer(text):
        y, mo, d = map(int, m.group(1,2,3))
        title = normalize(m.group(5))
        if len(title)>120: continue
        day=f'{y:04d}-{mo:02d}-{d:02d}'
        following=text[m.end():m.end()+100]
        tm=re.search(r'(?:開演|開始)\s*(\d{1,2}:\d{2})', following)
        ev=make_event(src,title,day,url,m.group(4),tm.group(1) if tm else '')
        if ev: found.append(ev)
    return list({e['id']:e for e in found}.values())


def walk_event(obj, out):
    if isinstance(obj, list):
        for x in obj: walk_event(x, out)
    elif isinstance(obj, dict):
        kinds = obj.get('@type', [])
        kinds = [kinds] if isinstance(kinds, str) else kinds
        if any(str(k).lower().endswith('event') for k in kinds):
            out.append(obj)
        for v in obj.values():
            if isinstance(v, (dict, list)): walk_event(v, out)


def structured(html, src, url):
    soup = BeautifulSoup(html, 'html.parser')
    raw = []
    for script in soup.find_all('script', type='application/ld+json'):
        try: walk_event(json.loads(script.string or script.get_text()), raw)
        except (ValueError, TypeError): pass
    out = []
    for item in raw:
        title, day = item.get('name'), str(item.get('startDate', ''))[:10]
        if not title or not re.fullmatch(r'20\d{2}-\d{2}-\d{2}', day):
            continue
        location = item.get('location', {})
        venue = location.get('name') if isinstance(location, dict) else ''
        source = dict(src)
        if venue: source['venue'] = venue
        ev = make_event(source, title, day, item.get('url') or url)
        if ev: out.append(ev)
    return out


def diagnose_page(html, src, url, method, response):
    """Print bounded, non-sensitive page structure hints for failed extraction."""
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'noscript', 'svg']):
        tag.decompose()
    title = normalize(soup.title.get_text(' ', strip=True)) if soup.title else ''
    headings = [normalize(t.get_text(' ', strip=True))[:100] for t in soup.select('h1,h2,h3,h4')][:12]
    text = normalize(soup.get_text(' ', strip=True))
    patterns = [r'20\d{2}年\s*\d{1,2}月\s*\d{1,2}日',
                r'20\d{2}[-/]\d{1,2}[-/]\d{1,2}',
                r'\d{1,2}月\s*\d{1,2}日', r'\d{1,2}/\d{1,2}']
    samples = []
    for pat in patterns:
        for match in list(re.finditer(pat, text))[:3]:
            a, b = max(0, match.start()-85), min(len(text), match.end()+160)
            excerpt = text[a:b]
            if excerpt not in samples:
                samples.append(excerpt)
    candidate_links = []
    for a in soup.find_all('a', href=True):
        label = normalize(a.get_text(' ', strip=True))
        href = a.get('href', '')
        if (re.search(r'event|schedule|detail|公演|イベント|開催', href+' '+label, re.I)
                and label and len(candidate_links) < 10):
            candidate_links.append({'text': label[:65], 'href': href[:140]})
    report = {'venue': src.get('venue'), 'method': method, 'requestedUrl': url,
              'finalUrl': response.url, 'htmlBytes': len(html), 'title': title[:160],
              'headings': headings, 'dateSamples': samples[:8],
              'candidateLinks': candidate_links, 'textStart': text[:450],
              'jsonLdCount': len(BeautifulSoup(html, 'html.parser').find_all('script', type='application/ld+json'))}
    print('DIAGNOSTIC ' + json.dumps(report, ensure_ascii=False), flush=True)


def main():
    sources = read_json('sources.json', [])
    old_events = read_json('data/auto-events.json', [])
    old_state = read_json('data/source-state.json', {})
    old_alerts = read_json('data/change-alerts.json', [])
    events = {e['id']: e for e in old_events if isinstance(e, dict) and e.get('id')}
    alerts, states = [], {}
    fetched = extracted = 0
    for src in sources:
        base = src.get('url', '')
        if not base: continue
        host = urlparse(base).netloc.lower()
        if 'kyoceradome-osaka.jp' in host:
            targets = [('https://www.kyoceradome-osaka.jp/schedule/?monthId=%d&yearId=%d' % (m, y), 'kyocera') for y, m in months()]
        elif 'osaka-johall.com' in host:
            targets = [('https://www.osaka-johall.com/event/?ym=%04d%02d' % (y, m), 'johall') for y, m in months()]
        else:
            targets = [(base, 'structured')]
        for url, method in targets:
            try:
                response = requests.get(url, headers=HEADERS, timeout=20)
                response.raise_for_status()
                response.encoding = response.apparent_encoding or response.encoding
                html = response.text
                digest = hashlib.sha256(html.encode('utf-8')).hexdigest()
                prev = old_state.get(url, {})
                if prev.get('hash') and prev['hash'] != digest:
                    alerts.append({'id': 'change-' + hashlib.sha256((url + digest).encode()).hexdigest()[:16],
                                   'type': 'page_changed', 'title': src['name'] + ' のページ変更を検知',
                                   'message': 'ページの変更を検知。イベントの変更・中止を意味するとは限りません。',
                                   'source': src['name'], 'url': url, 'detectedAt': NOW, 'status': '要確認'})
                parsed = kyocera(html, src, url) if method == 'kyocera' else (johall(html, src, url) if method == 'johall' else structured(html, src, url))
                if not parsed and (url == targets[0][0]):
                    diagnose_page(html, src, url, method, response)
                fetched += 1
                extracted += len(parsed)
                states[url] = {'hash': digest, 'checkedAt': NOW, 'status': 'ok', 'extracted': len(parsed)}
                for ev in parsed:
                    before = events.get(ev['id'])
                    if before and (before.get('time') != ev['time'] or before.get('status') != ev['status']):
                        alerts.append({'id': 'event-change-' + hashlib.sha256((ev['id'] + ev['time'] + ev['status']).encode()).hexdigest()[:16],
                                       'type': 'event_changed', 'title': ev['title'] + ' の内容変更候補',
                                       'message': '開演時刻または状態が変更された可能性があります。公式情報をご確認ください。',
                                       'source': src['name'], 'url': url, 'detectedAt': NOW, 'status': '要確認'})
                    events[ev['id']] = ev
            except Exception as exc:
                states[url] = {'checkedAt': NOW, 'status': 'error', 'error': str(exc)[:300]}
                alerts.append({'id': 'fetch-error-' + hashlib.sha256((url + TODAY.isoformat()).encode()).hexdigest()[:16],
                               'type': 'fetch_error', 'title': src['name'] + ' の取得に失敗',
                               'message': 'サイト取得失敗。中止とは判断しないでください。',
                               'source': src['name'], 'url': url, 'detectedAt': NOW, 'status': '要確認'})
    # Retain previous records on errors; never infer cancellation from absence.
    write_json('auto-events.json', sorted(events.values(), key=lambda e: (e.get('start', ''), e.get('title', ''))))
    merged = {a.get('id'): a for a in old_alerts if isinstance(a, dict) and a.get('id')}
    merged.update({a['id']: a for a in alerts})
    write_json('change-alerts.json', sorted(merged.values(), key=lambda a: a.get('detectedAt', ''), reverse=True)[:300])
    write_json('source-state.json', states)
    write_json('collector-status.json', {'checkedAt': NOW, 'sources': len(sources), 'fetched': fetched,
               'structuredEventsExtractedThisRun': extracted, 'totalAutoEvents': len(events),
               'newAlerts': len(alerts), 'note': '京セラドーム・大阪城ホールの本文抽出は試験運用。インテックスは構造化データのみ。イベント消失から中止を推定しません。'})
    print(json.dumps({'fetched': fetched, 'extracted': extracted, 'alerts': len(alerts)}, ensure_ascii=False))


if __name__ == '__main__':
    main()

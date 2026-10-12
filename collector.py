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
    # Prefer the event detail area; exclude calendar navigation which repeats dates.
    lines = [normalize(s) for s in soup.get_text('\n').splitlines()]
    lines = [s for s in lines if s]
    try:
        start = lines.index('DETAIL')
        lines = lines[start + 1:]
    except ValueError:
        pass
    for marker in ('BY DATE', '日程から探す'):
        if marker in lines:
            lines = lines[:lines.index(marker)]
            break
    found = []
    i = 0
    while i < len(lines):
        match = DATE.fullmatch(lines[i])
        if not match:
            i += 1
            continue
        day = '%04d-%02d-%02d' % tuple(map(int, match.groups()))
        j = i + 1
        while j < len(lines) and not DATE.fullmatch(lines[j]) and j - i < 35:
            j += 1
        chunk = lines[i + 1:j]
        if '日時' not in chunk:
            i = max(j, i + 1)
            continue
        heading = chunk[:chunk.index('日時')]
        ignore = {'コンサート', '野球', 'エンターテインメント', '販売・展示・その他', 'その他のスポーツ', 'アリーナ', 'スカイホール', '詳細を見る'}
        names = [s for s in heading if s not in ignore and not s.startswith('開場') and not s.startswith('開始')]
        title = names[0] if names else ''
        if title == '非公開イベント':
            i = max(j, i + 1)
            continue
        category = next((s for s in heading if s in ignore), 'その他')
        times = re.findall(r'(?:開始時間|開演)\s*[:：]?\s*(\d{1,2}:\d{2})', ' '.join(chunk))
        ev = make_event(src, title, day, url, category, times[0] if times else '')
        if ev:
            found.append(ev)
        i = max(j, i + 1)
    return found


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
            targets = [('https://www.osaka-johall.com/event/?ym=%04d%02d' % (y, m), 'structured') for y, m in months()]
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
                parsed = kyocera(html, src, url) if method == 'kyocera' else structured(html, src, url)
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
               'newAlerts': len(alerts), 'note': '京セラドームの本文抽出は試験運用。大阪城ホール・インテックスは構造化データのみ。イベント消失から中止を推定しません。'})
    print(json.dumps({'fetched': fetched, 'extracted': extracted, 'alerts': len(alerts)}, ensure_ascii=False))


if __name__ == '__main__':
    main()

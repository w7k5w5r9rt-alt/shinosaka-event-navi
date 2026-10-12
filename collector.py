"""Shinosaka Event Navi collector (v1.2): Kyocera, Osaka-Jo Hall, Intex Osaka.
Requires requests and beautifulsoup4. Run: python collector.py
Intex supplementary listings are attributed to Cosmosquare Development Council.
"""
import hashlib
import json
import os
import re
from datetime import date, datetime, timezone, timedelta
from urllib.parse import urlparse, urljoin
import requests
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, 'data')
os.makedirs(DATA, exist_ok=True)
NOW = datetime.now(timezone.utc).isoformat()
JST = timezone(timedelta(hours=9))
TODAY = datetime.now(JST).date()
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; ShinosakaEventNavi/1.2; event information verification)'}
INTEX_SUPPLEMENT = 'https://www.cosmosquare-djc.jp/eventinfo/'


def read_json(name, default):
    try:
        path = os.path.join(DATA, name[5:]) if name.startswith('data/') else os.path.join(ROOT, name)
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(name, obj):
    with open(os.path.join(DATA, name), 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def months(n=6):
    y, m = TODAY.year, TODAY.month
    for offset in range(n):
        yield y + (m - 1 + offset) // 12, (m - 1 + offset) % 12 + 1


def normalize(s):
    return re.sub(r'\s+', ' ', str(s)).strip()


def event_id(venue, title, day):
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
            'notes': '公開情報から自動抽出。日時・変更・中止は主催者・会場の公式サイトで再確認。' + (' ' + notes if notes else ''),
            'source': src['name'], 'verified': False, 'autoCollected': True,
            'status': 'scheduled', 'checkedAt': NOW}


def kyocera(html, src, url):
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'noscript', 'svg']): tag.decompose()
    text = normalize(soup.get_text(' ', strip=True))
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
    return list({e['id']: e for e in result}.values())


def johall(html, src, url):
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'noscript', 'svg']): tag.decompose()
    text = normalize(soup.get_text(' ', strip=True))
    pat = re.compile(r'(20\d{2})/(\d{1,2})/(\d{1,2})\s+(音楽・芸能|集会・式典|スポーツ|展示・販売|その他|イベント)\s+(.{2,180}?)\s+(?:開場|開演|開始)\s+')
    found = []
    for m in pat.finditer(text):
        y, mo, d = map(int, m.group(1, 2, 3))
        title = normalize(m.group(5))
        if len(title) > 120: continue
        day = f'{y:04d}-{mo:02d}-{d:02d}'
        following = text[m.end():m.end()+100]
        tm = re.search(r'(?:開演|開始)\s*(\d{1,2}:\d{2})', following)
        ev = make_event(src, title, day, url, m.group(4), tm.group(1) if tm else '')
        if ev: found.append(ev)
    return list({e['id']: e for e in found}.values())


def walk_event(obj, out):
    if isinstance(obj, list):
        for x in obj: walk_event(x, out)
    elif isinstance(obj, dict):
        kinds = obj.get('@type', [])
        kinds = [kinds] if isinstance(kinds, str) else kinds
        if any(str(k).lower().endswith('event') for k in kinds): out.append(obj)
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
        if not title or not re.fullmatch(r'20\d{2}-\d{2}-\d{2}', day): continue
        location = item.get('location', {})
        venue = location.get('name') if isinstance(location, dict) else ''
        source = dict(src)
        if venue: source['venue'] = venue
        ev = make_event(source, title, day, item.get('url') or url)
        if ev: out.append(ev)
    return out


def event_days(start, end):
    """Return bounded, validated daily dates for a multi-day exhibition."""
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        return []
    span = (last - first).days
    if span < 0 or span > 30: return []
    return [(first + timedelta(days=i)).isoformat() for i in range(span+1)]


def parse_intex_period(raw, default_year):
    """Parse Japanese exhibition date ranges; reject ambiguous/invalid dates."""
    s = normalize(raw).replace('～', '〜').replace('−', '〜').replace('－', '〜').replace('~', '〜')
    parts = re.findall(r'(?:(20\d{2})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日', s)
    if not parts:
        parts = re.findall(r'(?:(20\d{2})\s*[/.-]\s*)?(\d{1,2})\s*[/.-]\s*(\d{1,2})', s)
    if not parts: return []
    try:
        sy, sm, sd = parts[0]
        sy = int(sy or default_year)
        first = date(sy, int(sm), int(sd))
        ey, em, ed = parts[-1]
        ey = int(ey or sy)
        last = date(ey, int(em), int(ed))
        if not ey and last < first and first.month >= 11 and last.month <= 2:
            last = date(sy+1, int(em), int(ed))
    except ValueError:
        return []
    return event_days(first.isoformat(), last.isoformat())


def intex_supplement(html, src, url):
    """Parse event rows from Cosmosquare Development Council's Intex timetable.

    The page is a supplementary source, not the Intex official event database.
    It contains multiple tables, so only rows in an Intex-labelled section are read.
    """
    soup = BeautifulSoup(html, 'html.parser')
    result = []
    current_year = TODAY.year
    in_intex_section = False
    for node in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'table']):
        if node.name != 'table':
            heading = normalize(node.get_text(' ', strip=True))
            year = re.search(r'(20\d{2})年', heading)
            if year: current_year = int(year.group(1))
            if 'インテックス大阪' in heading:
                in_intex_section = True
            elif re.search(r'(?:ATC|大阪南港|その他のイベント|コスモスクエア)', heading) and 'インテックス大阪' not in heading:
                in_intex_section = False
            continue
        # Some pages place "インテックス大阪 2026年10月～11月分予定表" inside a table caption/header.
        table_text = normalize(node.get_text(' ', strip=True))
        table_heading = normalize(node.find('caption').get_text(' ', strip=True)) if node.find('caption') else ''
        head = ' '.join(normalize(r.get_text(' ', strip=True)) for r in node.find_all('tr', limit=3))
        if 'インテックス大阪' in (table_heading + ' ' + head[:200]): in_intex_section = True
        year = re.search(r'(20\d{2})年', table_heading + ' ' + head[:200])
        if year: current_year = int(year.group(1))
        # Require an event table header, not a generic calendar or unrelated content.
        if not (in_intex_section and '催' in head and ('会期' in head or '開催日' in head)):
            continue
        for row in node.find_all('tr'):
            cells = row.find_all(['td', 'th'], recursive=False)
            if len(cells) < 2: continue
            title = normalize(cells[0].get_text(' ', strip=True))
            period = normalize(cells[1].get_text(' ', strip=True))
            if not title or '催物' in title or title in ('催 物 名 称', 'イベント名'): continue
            days = parse_intex_period(period, current_year)
            if not days: continue
            times = normalize(cells[2].get_text(' ', strip=True)) if len(cells) > 2 else ''
            tm = re.search(r'(?<!\d)(\d{1,2}):(\d{2})', times)
            start_time = f'{int(tm.group(1)):02d}:{tm.group(2)}' if tm and int(tm.group(1)) < 24 else ''
            link = next((a.get('href') for c in cells[3:] for a in c.find_all('a', href=True)), None)
            event_url = urljoin(url, link) if link else url
            # Keep one event per date to fit the existing app's auto-event schema.
            for day in days:
                if day < TODAY.isoformat() or day > (TODAY + timedelta(days=190)).isoformat(): continue
                ev = make_event(src, title, day, event_url, '展示会・見本市', start_time,
                                '補助出典：コスモスクエア開発協議会のインテックス大阪予定表。')
                if ev: result.append(ev)
    return list({e['id']: e for e in result}.values())


def diagnose_page(html, src, url, method, response):
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'noscript', 'svg']): tag.decompose()
    txt = normalize(soup.get_text(' ', strip=True))
    headings = [normalize(x.get_text(' ', strip=True))[:100] for x in soup.select('h1,h2,h3')][:8]
    print('DIAGNOSTIC ' + json.dumps({'venue': src.get('venue'), 'method': method,
        'requestedUrl': url, 'finalUrl': response.url, 'htmlBytes': len(html),
        'headings': headings, 'textStart': txt[:350]}, ensure_ascii=False), flush=True)


def main():
    sources = read_json('sources.json', [])
    old_events = read_json('data/auto-events.json', [])
    old_state = read_json('data/source-state.json', {})
    old_alerts = read_json('data/change-alerts.json', [])
    events = {e['id']: e for e in old_events if isinstance(e, dict) and e.get('id')}
    alerts, states = [], {}
    fetched = extracted = 0
    targets = []
    has_intex = False
    for src in sources:
        base = src.get('url', '')
        if not base: continue
        host = urlparse(base).netloc.lower()
        if 'kyoceradome-osaka.jp' in host:
            targets += [(f'https://www.kyoceradome-osaka.jp/schedule/?monthId={m}&yearId={y}', 'kyocera', src) for y, m in months()]
        elif 'osaka-johall.com' in host:
            targets += [(f'https://www.osaka-johall.com/event/?ym={y:04d}{m:02d}', 'johall', src) for y, m in months()]
        elif 'intex-osaka.com' in host:
            has_intex = True
            targets.append((base, 'structured', src))
        else:
            targets.append((base, 'structured', src))
    if has_intex:
        targets.append((INTEX_SUPPLEMENT, 'intex_supplement', {
            'name': 'コスモスクエア開発協議会（インテックス大阪予定表）', 'venue': 'インテックス大阪'}))

    for url, method, src in targets:
        try:
            response = requests.get(url, headers=HEADERS, timeout=25)
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
            if method == 'kyocera': parsed = kyocera(html, src, url)
            elif method == 'johall': parsed = johall(html, src, url)
            elif method == 'intex_supplement': parsed = intex_supplement(html, src, url)
            else: parsed = structured(html, src, url)
            if not parsed and method in ('structured', 'intex_supplement'):
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
                        'source': src['name'], 'url': ev.get('url') or url, 'detectedAt': NOW, 'status': '要確認'})
                events[ev['id']] = ev
        except Exception as exc:
            states[url] = {'checkedAt': NOW, 'status': 'error', 'error': str(exc)[:300]}
            alerts.append({'id': 'fetch-error-' + hashlib.sha256((url + TODAY.isoformat()).encode()).hexdigest()[:16],
                'type': 'fetch_error', 'title': src['name'] + ' の取得に失敗',
                'message': 'サイト取得失敗。中止とは判断しないでください。',
                'source': src['name'], 'url': url, 'detectedAt': NOW, 'status': '要確認'})
    # Retain prior records on errors; disappearance alone does not imply cancellation.
    write_json('auto-events.json', sorted(events.values(), key=lambda e: (e.get('start', ''), e.get('title', ''))))
    merged = {a.get('id'): a for a in old_alerts if isinstance(a, dict) and a.get('id')}
    merged.update({a['id']: a for a in alerts})
    write_json('change-alerts.json', sorted(merged.values(), key=lambda a: a.get('detectedAt', ''), reverse=True)[:300])
    write_json('source-state.json', states)
    write_json('collector-status.json', {'checkedAt': NOW, 'sources': len(targets), 'fetched': fetched,
        'structuredEventsExtractedThisRun': extracted, 'totalAutoEvents': len(events),
        'newAlerts': len(alerts), 'note': 'インテックス大阪は公式JSON-LDに加え、コスモスクエア開発協議会の予定表を補助出典として利用。消失から中止を推定しません。'})
    print(json.dumps({'fetched': fetched, 'extracted': extracted, 'alerts': len(alerts),
                      'intexSupplementEnabled': has_intex}, ensure_ascii=False))


if __name__ == '__main__':
    main()

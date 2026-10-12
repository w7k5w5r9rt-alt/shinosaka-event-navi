"""Shinosaka Event Navi collector (v1.4): Kyocera, Osaka-Jo Hall, Intex Osaka.
Requires requests and beautifulsoup4. Run: python collector.py
Intex supplementary listings are attributed to Cosmosquare Development Council.
"""
import hashlib
import json
import os
import re
from io import BytesIO
import pdfplumber
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
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; ShinosakaEventNavi/1.3; event information verification)'}
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


def explicit_status(text):
    """Only act on explicit announcement language in a collected event title."""
    t = normalize(text)
    if re.search(r'(?:公演|開催|イベント|試合|大会)?(?:中止|開催中止|公演中止|中止のお知らせ|CANCELLED|CANCELED)', t, re.I):
        return 'cancelled'
    if re.search(r'(?:公演|開催|イベント|試合|大会)?(?:延期|開催延期|公演延期|延期のお知らせ|POSTPONED)', t, re.I):
        return 'postponed'
    return 'scheduled'


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
            'status': explicit_status(title + ' ' + notes), 'checkedAt': NOW}


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
    """Read the requested month's Osaka-Jo Hall table, not the upcoming highlights."""
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'noscript', 'svg']):
        tag.decompose()
    text = normalize(soup.get_text(' ', strip=True))
    ym = re.search(r'[?&]ym=(20\d{2})(0[1-9]|1[0-2])', url)
    if not ym:
        print('JOHALL missing ym', url, flush=True)
        return []
    year, month = map(int, ym.groups())
    # Some page templates separate the year, 年, month, 月 with whitespace.
    heading = re.search(rf'{year}\s*年\s*0?{month}\s*月\s*の\s*イベント', text)
    if not heading:
        print('JOHALL monthly heading missing', url, 'headings',
              re.findall(r'.{0,12}月.{0,12}イベント', text)[:5], flush=True)
        return []
    section = text[heading.end():]
    section = re.split(r'\s+(?:トップ|OSAKA-JO HALL|会社情報)\s+', section, maxsplit=1)[0]
    day_re = re.compile(r'(?<!\d)([1-9]|[12]\d|3[01])\s*[（(][月火水木金土日][）)]')
    days = list(day_re.finditer(section))
    found = {}
    for i, m in enumerate(days):
        try:
            d = date(year, month, int(m.group(1)))
        except ValueError:
            continue
        if not (TODAY <= d <= TODAY + timedelta(days=190)):
            continue
        segment = section[m.end():(days[i+1].start() if i+1 < len(days) else len(section))].strip()
        title = re.split(r'\s+(?:開場|開演|開始|座席|主催|音楽[・･]芸能|集会[・･]式典|物販[・･]展示|スポーツ)\s*', segment, maxsplit=1)[0].strip()
        if not (3 <= len(title) <= 150):
            continue
        tm = re.search(r'(?:開演|開始)\s*[:：]?\s*(\d{1,2}:\d{2})', segment)
        cat = '音楽・芸能' if re.search(r'LIVE|ライブ|コンサート|TOUR|音楽・芸能', segment, re.I) else 'その他'
        ev = make_event(src, title, d.isoformat(), url, cat, tm.group(1) if tm else '')
        if ev:
            found[ev['id']] = ev
    print('JOHALL', url, 'monthly rows', len(days), 'extracted', len(found),
          'titles', [e['title'] for e in found.values()][:25], flush=True)
    return list(found.values())

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
    # Japanese schedules often omit the month on the end date: 10月14日〜16日.
    if len(parts) == 1:
        short_end = re.search(r'[〜～~\-]\s*(\d{1,2})\s*日', s)
        if short_end:
            parts.append(('',parts[0][1],short_end.group(1)))
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


def parse_intex_rows(rows, src, source_url, year):
    """Only consume rows already scoped to the Intex timetable."""
    found = []
    for cells in rows:
        if len(cells) < 3: continue
        title, period, hours = (normalize(str(x or '').replace('\n', ' ')) for x in cells[:3])
        if not title or '催物名称' in title.replace(' ', '') or len(title) > 250: continue
        days = parse_intex_period(period, year)
        if not days: continue
        tm = re.search(r'(?<!\d)([01]?\d|2[0-3])[:：]([0-5]\d)', hours)
        start_time = f'{int(tm.group(1)):02d}:{tm.group(2)}' if tm else ''
        link = next((str(x).strip() for x in cells[3:] if str(x or '').strip().startswith(('https://','http://'))), source_url)
        for day in days:
            if TODAY.isoformat() <= day <= (TODAY + timedelta(days=190)).isoformat():
                ev = make_event(src, title, day, link, '展示会・見本市', start_time,
                                '補助出典：コスモスクエア開発協議会のインテックス大阪予定表。')
                if ev: found.append(ev)
    return found


def intex_supplement(html, src, url):
    """Read Intex-only HTML tables or the linked timetable PDF (never ATC rows)."""
    soup = BeautifulSoup(html, 'html.parser')
    found = []
    year = TODAY.year
    # Some pages render the PDF's text as HTML tables; only accept Intex-labelled tables.
    section = False
    for node in soup.find_all(['h1','h2','h3','h4','h5','p','table']):
        if node.name != 'table':
            heading = normalize(node.get_text(' ', strip=True))
            if 'インテックス大阪' in heading and ('予定表' in heading or 'イベント' in heading):
                section = True
                m = re.search(r'(20\d{2})年', heading)
                if m: year = int(m.group(1))
            elif 'ATCイベントスケジュール' in heading:
                section = False
            continue
        heading = normalize(node.get_text(' ', strip=True)[:250])
        if 'インテックス大阪' in heading and '予定表' in heading:
            section = True
            m = re.search(r'(20\d{2})年', heading)
            if m: year = int(m.group(1))
        if not section: continue
        rows = []
        for tr in node.find_all('tr'):
            cells = tr.find_all(['td','th'], recursive=False)
            row = [c.get_text(' ',strip=True) for c in cells]
            for c in cells[3:]:
                a = c.find('a',href=True)
                if a: row.append(urljoin(url,a['href']))
            rows.append(row)
        found.extend(parse_intex_rows(rows,src,url,year))
    if found: return list({e['id']:e for e in found}.values())

    # Published event timetable is commonly a linked PDF rather than HTML tables.
    pdf_links = []
    for a in soup.find_all('a',href=True):
        href = urljoin(url,a['href'])
        label = normalize(a.get_text(' ',strip=True))
        if urlparse(href).netloc.endswith('cosmosquare-djc.jp') and ('.pdf' in href.lower()):
            pdf_links.append((href,label))
    # Also support embedded PDF links (iframe/embed/object).
    for node in soup.select('iframe[src],embed[src],object[data]'):
        href = urljoin(url,node.get('src') or node.get('data'))
        if '.pdf' in href.lower() and urlparse(href).netloc.endswith('cosmosquare-djc.jp'):
            pdf_links.append((href,''))
    # Current issue first; avoid excessive downloads and stale issues.
    for pdf_url, _ in list(dict(pdf_links).items())[:3]:
        try:
            response = requests.get(pdf_url,headers=HEADERS,timeout=25)
            response.raise_for_status()
            if not response.content.startswith(b'%PDF'): continue
            with pdfplumber.open(BytesIO(response.content)) as pdf:
                for page in pdf.pages[:4]:
                    page_text = page.extract_text() or ''
                    m = re.search(r'インテックス大阪\s*(20\d{2})年',page_text)
                    if not m: continue
                    pdf_year = int(m.group(1))
                    for table in page.extract_tables():
                        rows = [[normalize(c or '') for c in row] for row in table if row]
                        if not rows: continue
                        # Only the table with Intex exhibition headers, not ATC schedule.
                        header = ' '.join(' '.join(r) for r in rows[:2]).replace(' ','')
                        if '催物名称' not in header or '会期' not in header: continue
                        found.extend(parse_intex_rows(rows,src,pdf_url,pdf_year))
                    if found: break
        except Exception as exc:
            print('INTEX PDF WARNING '+str(exc)[:180],flush=True)
        if found: break
    print('INTEX timetable extracted '+str(len(found))+' events',flush=True)
    return list({e['id']:e for e in found}.values())

def expo_park(html, src, url):
    """Conservative extraction from official expo70-park event list.

    Only linked event headings with a nearby explicit Japanese date are accepted.
    Long exhibitions are retained as one range, not fabricated daily events.
    """
    soup = BeautifulSoup(html, 'html.parser')
    out = []
    for a in soup.select('a[href]'):
        href = urljoin(url, a.get('href', ''))
        if urlparse(href).netloc not in ('www.expo70-park.jp', 'expo70-park.jp'): continue
        title = normalize(a.get_text(' ', strip=True))
        if not (5 <= len(title) <= 130): continue
        if not re.search(r'/event(?:s)?/', urlparse(href).path): continue
        if href.rstrip('/') == url.rstrip('/'): continue
        parent = a
        # Official event cards typically place the date adjacent to the title.
        for _ in range(3):
            if parent.parent is None: break
            parent = parent.parent
            txt = normalize(parent.get_text(' ', strip=True))
            if len(txt) > 550: break
            dates = re.findall(r'(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日', txt)
            if not dates: continue
            try: start = date(*map(int, dates[0])).isoformat()
            except ValueError: break
            if not (TODAY.isoformat() <= start <= (TODAY+timedelta(days=190)).isoformat()): break
            ev = make_event(src, title, start, href, '公園イベント', notes='公式一覧から取得。期間や実施日を詳細ページで確認。')
            if ev: out.append(ev)
            break
    return list({e['id']: e for e in out}.values())



def gamba_games(html, src, url):
    """Gamba official fixtures: HOME matches at Panasonic Stadium only.
    Away fixtures and other venues are deliberately excluded.
    """
    soup = BeautifulSoup(html, 'html.parser')
    for t in soup(['script','style','noscript','svg']): t.decompose()
    text = normalize(soup.get_text(' ', strip=True))
    # Official listing: HOME 10.24(土) 16:00 ＠パナスタ ... vs. 京都サンガF.C.
    pattern = re.compile(r'\bHOME\s+(\d{1,2})\.(\d{1,2})\s*[（(][月火水木金土日][）)]\s+(\d{1,2}:\d{2})\s+＠\s*(パナスタ|吹田S|吹田Ｓ|パナソニック\s*スタジアム\s*吹田)(.{0,300}?)\bvs[.．]\s*(.{2,70}?)(?=\s+(?:試合情報|FC\d|一般販売|チケット購入|HOME|AWAY|明治安田|AFC|天皇杯|Jリーグ)|$)',re.I)
    found=[]
    for m in pattern.finditer(text):
        month,day=map(int,m.group(1,2))
        # season crosses calendar year; use nearest future year within 190 days
        dates=[]
        for year in (TODAY.year,TODAY.year+1):
            try: dt=date(year,month,day)
            except ValueError: continue
            if TODAY <= dt <= TODAY+timedelta(days=190): dates.append(dt)
        if not dates: continue
        dt=min(dates)
        opponent=normalize(m.group(6)).strip(' ・-')
        if not opponent or len(opponent)>55: continue
        ev=make_event(src,'ガンバ大阪 vs '+opponent,dt.isoformat(),url,'スポーツ',m.group(3),notes='公式試合日程のHOME表記と会場表記を照合。来場者数は未公表。')
        if ev: found.append(ev)
    print('GAMBA home matches extracted '+str(len(found)),flush=True)
    return list({e['id']:e for e in found}.values())

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
        elif 'gamba-osaka.net' in host:
            targets.append((base, 'gamba_games', src))
        elif 'expo70-park.jp' in host:
            targets.append((base, 'expo_park', src))
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
            elif method == 'expo_park': parsed = expo_park(html, src, url)
            elif method == 'gamba_games': parsed = gamba_games(html, src, url)
            else: parsed = structured(html, src, url)
            if not parsed and method in ('johall', 'kyocera', 'structured', 'intex_supplement', 'expo_park', 'gamba_games'):
                diagnose_page(html, src, url, method, response)
            fetched += 1
            extracted += len(parsed)
            states[url] = {'hash': digest, 'checkedAt': NOW, 'status': 'ok' if parsed else 'zero_extracted', 'extracted': len(parsed)}
            if not parsed and method in ('johall', 'kyocera'):
                alerts.append({'id': 'zero-events-' + hashlib.sha256((url + TODAY.isoformat()).encode()).hexdigest()[:16],
                    'type': 'zero_extracted', 'title': src['name'] + ' の抽出件数が0件',
                    'message': 'ページ取得は成功しましたが、イベントを読み取れませんでした。公式日程との照合が必要です。',
                    'source': src['name'], 'url': url, 'detectedAt': NOW, 'status': '要確認'})
            for ev in parsed:
                before = events.get(ev['id'])
                if before and before.get('status') in ('cancelled','postponed') and ev['status']=='scheduled':
                    # Do not reverse an explicit cancellation just because a page still lists the event.
                    ev['status']=before['status']
                if before and (before.get('time') != ev['time'] or before.get('status') != ev['status']):
                    alerts.append({'id': 'event-change-' + hashlib.sha256((ev['id'] + ev['time'] + ev['status']).encode()).hexdigest()[:16],
                        'type': 'event_changed', 'title': ev['title'] + ' の内容変更候補',
                        'message': '開演時刻または状態が変更された可能性があります。公式情報をご確認ください。',
                        'source': src['name'], 'url': ev.get('url') or url, 'detectedAt': NOW, 'status': '要確認'})
                events[ev['id']] = ev
                if ev['status'] in ('cancelled','postponed') and (not before or before.get('status') != ev['status']):
                    alerts.append({'id':'status-'+hashlib.sha256((ev['id']+ev['status']).encode()).hexdigest()[:16],
                        'type':'explicit_status','title':ev['title']+' の'+('中止' if ev['status']=='cancelled' else '延期')+'表記を検知',
                        'message':'収集元のイベント表記に中止・延期の文言があります。公式情報で再確認してください。',
                        'source':src['name'],'url':ev.get('url') or url,'detectedAt':NOW,'status':'要確認'})
        except Exception as exc:
            states[url] = {'checkedAt': NOW, 'status': 'error', 'error': str(exc)[:300]}
            alerts.append({'id': 'fetch-error-' + hashlib.sha256((url + TODAY.isoformat()).encode()).hexdigest()[:16],
                'type': 'fetch_error', 'title': src['name'] + ' の取得に失敗',
                'message': 'サイト取得失敗。中止とは判断しないでください。',
                'source': src['name'], 'url': url, 'detectedAt': NOW, 'status': '要確認'})
    # Repair old Osaka-Jo Hall URLs accidentally pointing to the last crawled month.
    for ev in events.values():
        if ev.get('venue') == '大阪城ホール' and re.fullmatch(r'20\d{2}-\d{2}-\d{2}',ev.get('start','')):
            ev['url'] = 'https://www.osaka-johall.com/event/?ym=' + ev['start'][:7].replace('-','')
    # Retain prior records on errors; disappearance alone does not imply cancellation.
    write_json('auto-events.json', sorted(events.values(), key=lambda e: (e.get('start', ''), e.get('title', ''))))
    # Retain actionable notices; collapse page changes to one latest notice per URL.
    # A changed page is not evidence of event cancellation or rescheduling.
    merged = {}
    for a in [*old_alerts, *alerts]:
        if not isinstance(a, dict): continue
        typ = a.get('type', '')
        if typ == 'page_changed':
            key = ('page_changed', (a.get('source') or a.get('title') or a.get('url') or '').split(' のページ変更を検知')[0].strip())
            a = dict(a, priority='info')
        elif typ in ('explicit_status', 'event_changed'):
            key = ('important', a.get('id'))
            a = dict(a, priority='important')
        elif typ in ('fetch_error', 'zero_extracted'):
            key = (typ, a.get('url') or a.get('source'))
            a = dict(a, priority='info')
        else:
            key = ('other', a.get('id') or a.get('title'))
        if key[1] and (key not in merged or a.get('detectedAt','') >= merged[key].get('detectedAt','')):
            merged[key] = a
    notices = sorted(merged.values(), key=lambda a: (a.get('priority') == 'important', a.get('detectedAt','')), reverse=True)[:200]
    write_json('change-alerts.json', notices)
    write_json('source-state.json', states)
    write_json('collector-status.json', {'checkedAt': NOW, 'sources': len(targets), 'fetched': fetched,
        'structuredEventsExtractedThisRun': extracted, 'totalAutoEvents': len(events),
        'newAlerts': len(alerts), 'note': '万博記念公園とガンバ大阪ホームゲームも収集対象。来場者数は実測未確認。消失から中止を推定しません。'})
    print(json.dumps({'fetched': fetched, 'extracted': extracted, 'alerts': len(alerts),
                      'intexSupplementEnabled': has_intex}, ensure_ascii=False))


if __name__ == '__main__':
    main()

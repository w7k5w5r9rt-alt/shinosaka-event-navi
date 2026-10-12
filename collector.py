import json, hashlib, os, re
from datetime import datetime, timezone
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup

ROOT=os.path.dirname(os.path.abspath(__file__))
DATA=os.path.join(ROOT,'data')
os.makedirs(DATA,exist_ok=True)
HEADERS={'User-Agent':'ShinosakaEventNavi/1.0 (event schedule monitoring; contact: repository owner)'}
NOW=datetime.now(timezone.utc).isoformat()

def read_json(path, default):
    try:
        with open(path,encoding='utf-8') as f: return json.load(f)
    except Exception: return default

def write_json(path, obj):
    with open(path,'w',encoding='utf-8') as f: json.dump(obj,f,ensure_ascii=False,indent=2)

def walk_event(obj, out):
    if isinstance(obj,list):
        for x in obj: walk_event(x,out)
    elif isinstance(obj,dict):
        typ=obj.get('@type',[]); typ=[typ] if isinstance(typ,str) else typ
        if any(str(t).lower()=='event' for t in typ): out.append(obj)
        for k,v in obj.items():
            if k in ('@graph','itemListElement','mainEntity'): walk_event(v,out)

def parse_date(s):
    if not s: return ''
    s=str(s).strip()
    m=re.match(r'^(\d{4}-\d\d-\d\d)',s)
    return m.group(1) if m else ''

def extract_events(html, source):
    soup=BeautifulSoup(html,'html.parser'); found=[]
    for tag in soup.find_all('script',type='application/ld+json'):
        try: walk_event(json.loads(tag.string or tag.get_text()),found)
        except Exception: pass
    results=[]
    for i,e in enumerate(found):
        title=e.get('name')
        start=parse_date(e.get('startDate')); end=parse_date(e.get('endDate')) or start
        if not title or not start: continue
        loc=e.get('location') or {}; venue=loc.get('name','') if isinstance(loc,dict) else str(loc)
        address=''
        if isinstance(loc,dict):
            addr=loc.get('address')
            if isinstance(addr,dict): address=' '.join(str(addr.get(k,'')) for k in ('addressLocality','streetAddress')).strip()
        url=e.get('url') or source['url']
        status=str(e.get('eventStatus',''))
        status='cancelled' if 'EventCancelled' in status else ('postponed' if 'EventPostponed' in status else 'scheduled')
        results.append({'id':'auto-'+hashlib.sha256((str(title)+'|'+start+'|'+source['url']).encode()).hexdigest()[:16], 'title':str(title), 'start':start, 'end':end, 'time':'', 'category':'その他', 'venue':venue or source.get('venue','未設定'), 'scale':3, 'access':3, 'url':str(url), 'notes':'自動取得（公式ページの構造化データ）。運用前に公式情報を確認。'+((' 場所: '+address) if address else ''), 'source':source['name'], 'verified':False, 'autoCollected':True, 'status':status, 'checkedAt':NOW})
    return results

def main():
    sources=read_json(os.path.join(ROOT,'sources.json'),[])
    previous=read_json(os.path.join(DATA,'source-state.json'),{})
    old_events=read_json(os.path.join(DATA,'auto-events.json'),[])
    events_by_id={e.get('id'):e for e in old_events if e.get('id')}
    alerts=[]; new_state={}; fetched=0; extracted=0
    for src in sources:
        url=src.get('url',''); name=src.get('name',url)
        try:
            r=requests.get(url,headers=HEADERS,timeout=25); r.raise_for_status()
            html=r.text; digest=hashlib.sha256(html.encode('utf-8',errors='replace')).hexdigest()
            new_state[url]={'hash':digest,'checkedAt':NOW,'status':'ok'}; fetched+=1
            old=previous.get(url,{})
            if old.get('hash') and old.get('hash')!=digest:
                alerts.append({'id':'change-'+hashlib.sha256((url+digest).encode()).hexdigest()[:16], 'type':'page_changed','title':name+' のページ変更を検知', 'message':'ページ内容が前回取得時から変わりました。イベント日程の変更とは限らないため、公式ページを確認してください。', 'source':name,'url':url,'detectedAt':NOW,'status':'要確認'})
            parsed=extract_events(html,src); extracted+=len(parsed)
            for e in parsed:
                olde=events_by_id.get(e['id'])
                if olde:
                    if olde.get('start')!=e['start'] or olde.get('end')!=e['end'] or olde.get('status')!=e['status']:
                        alerts.append({'id':'event-change-'+e['id']+'-'+NOW[:10], 'type':'event_changed','title':e['title']+' の日程・状態変更候補','message':f"前回: {olde.get('start')}〜{olde.get('end')} / 今回: {e['start']}〜{e['end']} / 状態: {e.get('status')}",'source':name,'url':e['url'],'detectedAt':NOW,'status':'要確認'})
                events_by_id[e['id']]=e
        except Exception as ex:
            new_state[url]={'checkedAt':NOW,'status':'error','error':str(ex)[:300]}
            alerts.append({'id':'fetch-error-'+hashlib.sha256((url+NOW[:10]).encode()).hexdigest()[:16], 'type':'fetch_error','title':name+' の取得に失敗', 'message':'通信エラーまたはページ構造の変更の可能性があります。中止とは判断しないでください。','source':name,'url':url,'detectedAt':NOW,'status':'要確認'})
    # Keep current records and add a 90-day window only; never infer cancellation from disappearance.
    all_events=list(events_by_id.values())
    write_json(os.path.join(DATA,'auto-events.json'),all_events)
    prior_alerts=read_json(os.path.join(DATA,'change-alerts.json'),[])
    known={a.get('id') for a in prior_alerts}
    merged=alerts+[a for a in prior_alerts if a.get('id') not in {x.get('id') for x in alerts}]
    write_json(os.path.join(DATA,'change-alerts.json'),merged[:300])
    write_json(os.path.join(DATA,'source-state.json'),new_state)
    write_json(os.path.join(DATA,'collector-status.json'),{'checkedAt':NOW,'sources':len(sources),'fetched':fetched,'structuredEventsExtractedThisRun':extracted,'totalAutoEvents':len(all_events),'newAlerts':len(alerts),'note':'抽出件数は構造化データが掲載されている場合のみ。ページ変更だけでは日程変更・中止と断定しません。'})
    print(json.dumps({'fetched':fetched,'extracted':extracted,'alerts':len(alerts)},ensure_ascii=False))
if __name__=='__main__': main()

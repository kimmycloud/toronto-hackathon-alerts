#!/usr/bin/env python3
"""Public event discovery. Python 3.10+, pip install lxml.
Run: python discover.py --as-of 2026-09-28 > events.json
See README.md for coverage, test evidence and source limitations.
"""
import argparse
import datetime as dt
import hashlib
import html as html_std
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo
from lxml import html

TZ = ZoneInfo('America/Toronto')
UA = 'UniversityHackathonDiscovery/1.0'
HACK = re.compile(r'\bhackathons?\b|\b(?:datathon|makeathon|designathon)s?\b|\buoft?hacks\b|\bellehacks\b|\bdeltahacks\b|\buottahack\b|\bcuhacking\b|\bhack(?:rx| the north| the valley| the hill| the world)\b|\bspeed\s*hack\b', re.I)
MONTHS = {name.lower(): i for i, name in enumerate(['January','February','March','April','May','June','July','August','September','October','November','December'],1)}
MONTHS.update({k[:3]:v for k,v in list(MONTHS.items())})
MONTH = r'(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)'
DATE = re.compile(r'\b('+MONTH+r')\s+(\d{1,2}),?\s+(\d{4})\b',re.I)
RANGE = re.compile(r'\b('+MONTH+r')\s+(\d{1,2})\s*[-–—]\s*(\d{1,2}),?\s+(\d{4})\b',re.I)

def clean(s):
    return ' '.join(html_std.unescape(s or '').split())

def text(node):
    return clean(' '.join(node.xpath('.//text()[not(ancestor::script) and not(ancestor::style)]')))

def cls(name):
    return 'contains(concat(" ",normalize-space(@class)," ")," '+name+' ")'

def dates(s):
    """Parse ONLY explicit event date labels, never infer a year from today's date."""
    m=RANGE.search(s)
    if m:
        mo,a,b,y=m.groups()
        return dt.date(int(y),MONTHS[mo.lower()],int(a)).isoformat(),dt.date(int(y),MONTHS[mo.lower()],int(b)).isoformat()
    found=[dt.date(int(y),MONTHS[mo.lower()],int(day)).isoformat() for mo,day,y in DATE.findall(s)]
    return (found[0],found[-1]) if found else (None,None)

class Client:
    def __init__(self): self.log=[]; self.last={}
    def get(self,url):
        host=urllib.parse.urlsplit(url).netloc
        time.sleep(max(0,1-(time.monotonic()-self.last.get(host,0))))
        self.last[host]=time.monotonic()
        try:
            req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'application/json,text/html,application/rss+xml,text/calendar;q=0.9'})
            with urllib.request.urlopen(req,timeout=30) as r:
                b=r.read(12_000_001)
                if len(b)>12_000_000: raise ValueError('Response exceeds 12 MB limit')
                self.log.append({'url':url,'final_url':r.url,'http_status':r.status,'content_type':r.headers.get('Content-Type'),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()})
                return b,r.headers
        except urllib.error.HTTPError as e:
            self.log.append({'url':url,'http_status':e.code})
            raise RuntimeError(f'HTTP {e.code}; no authentication or challenge bypass attempted') from e
    def page(self,url):
        b,_=self.get(url)
        doc=html.fromstring(b)
        title=clean(' '.join(doc.xpath('//head/title/text()')))
        if any(x in title.lower() for x in ['site unavailable','access denied','just a moment','sign in']):
            raise ValueError(f'Unavailable/challenge/login page: {title}')
        return doc
    def json(self,url):
        b,h=self.get(url)
        if 'json' not in h.get('Content-Type','').lower(): raise ValueError('Expected JSON, received '+h.get('Content-Type','unknown'))
        return json.loads(b)

def event(school,source,title,url,start=None,end=None,raw_date='',description='',**extra):
    return dict(school=school,source=source,title=clean(title),url=url,start_date=start,end_date=end,date_text=clean(raw_date),description=clean(description),**extra)

def tmu_parse(j):
    if not isinstance(j,dict) or not isinstance(j.get('data'),list) or 'totalMatches' not in j: raise ValueError('TMU schema changed')
    rows=[]
    for x in j['data']:
        def day(s):
            # Java Date.toString: "Wed Sep 09 11:00:00 EDT 2026"; retain local calendar date.
            parts=s.split()
            if len(parts)!=6: raise ValueError('Unknown TMU date format: '+s)
            return dt.date(int(parts[5]),MONTHS[parts[1].lower()],int(parts[2])).isoformat()
        rows.append(event('TMU','tmu',x['title'],urllib.parse.urljoin('https://www.torontomu.ca',x['page'].removeprefix('/content/ryerson')),day(x['from']),day(x['to']),x['from']+' — '+x['to'],location=x.get('location'),registration_url=x.get('website')))
    return rows

def tmu(c):
    base='https://www.torontomu.ca/news-events/events/'
    d=c.page(base)
    scripts='\n'.join(d.xpath('//script[not(@src)]/text()'))
    m=re.search(r'initStackComponent\(\{.*?url:\s*"([^"]+)"',scripts,re.S)
    if not m: raise ValueError('TMU event stack configuration missing')
    # The site's JS strips /content/ryerson and uses page 0 when filtering is enabled.
    endpoint=urllib.parse.urljoin(base,m[1].removeprefix('/content/ryerson'))+'.data.0.json'
    j=c.json(endpoint); rows=tmu_parse(j)
    if len(rows)<int(j['totalMatches'])-int(j.get('originalOffset',0)):
        raise ValueError('TMU response truncated: pagination needs reinspection')
    return rows

def listing_parse(d,school,source,base):
    if source=='waterloo':
        cards=d.xpath('//article['+cls('card__teaser--event')+']')
        nodes=[(x,x.xpath('.//h2['+cls('card__title')+']/a'),x.xpath('.//*['+cls('uw-date')+']')) for x in cards]
    else:
        cards=d.xpath('//div['+cls('article-teaser__item-content')+']')
        nodes=[(x,x.xpath('.//h2/a'),x.xpath('.//div['+cls('article-teaser__item-body-wordwrap')+']/strong')) for x in cards]
    rows=[]
    for card,links,labels in nodes:
        if not links or not labels: raise ValueError(f'{source}: event card schema changed')
        label=text(labels[0]); a,b=dates(label)
        rows.append(event(school,source,text(links[0]),urllib.parse.urljoin(base,links[0].get('href')),a,b,label,text(card)))
    if not nodes: raise ValueError(f'{source}: no event cards; inspect empty-state or layout before treating as zero')
    return rows

def listing(c,school,source,url,max_pages):
    rows=[];visited=set()
    for _ in range(max_pages):
        if url in visited: raise ValueError('Pagination loop')
        visited.add(url);d=c.page(url)
        rows.extend(listing_parse(d,school,source,url))
        nxt=d.xpath('//a[@rel="next"]/@href | //li['+cls('pager__item--next')+']/a/@href')
        if not nxt:return rows
        next_url=urllib.parse.urljoin(url,nxt[0])
        if urllib.parse.urlsplit(next_url).netloc!=urllib.parse.urlsplit(url).netloc: raise ValueError('Cross-host pagination')
        url=next_url
    raise ValueError(f'{source}: max-pages reached; scan incomplete')

def carleton_parse(j,source='carleton'):
    if not isinstance(j,dict) or not isinstance(j.get('posts'),list):raise ValueError('Carleton schema changed')
    rows=[]
    for x in j['posts']:
        a=x.get('cu_event_start_date');b=x.get('cu_event_end_date')
        # These are local campus datetime strings, not UTC timestamps.
        for value in [a,b]:
            if value:dt.datetime.fromisoformat(value)
        rows.append(event('Carleton',source,x['title'],x['link'],a[:10] if a else None,b[:10] if b else None,clean(a)+' — '+clean(b)))
    if int(j.get('pagination',{}).get('total',len(rows)))>len(rows):raise ValueError('Carleton response truncated')
    return rows

def carleton(c):
    return carleton_parse(c.json('https://events.carleton.ca/wp-json/cutheme/v1/cu-calendar'))

DOMAINS={'uofthacks.com':'U of T','makeuoft.ca':'U of T','hackthevalley.io':'U of T','ellehacks.com':'York','hackthenorth.com':'Waterloo','deltahacks.com':'McMaster','uottahack.ca':'uOttawa','cuhacking.ca':'Carleton'}
def mlh_parse(d,source_url):
    cards=d.xpath('//*[@itemscope and @itemtype="https://schema.org/Event"]')
    if not cards:raise ValueError('MLH Event microdata missing')
    out=[]
    for card in cards:
        def prop(name):
            v=card.xpath('.//*[@itemprop="'+name+'"]/@content');return v[0] if v else None
        url=prop('url')
        if not url:raise ValueError('MLH event URL missing')
        host=urllib.parse.urlsplit(url).netloc.lower().removeprefix('www.')
        school=next((s for dom,s in DOMAINS.items() if host==dom or host.endswith('.'+dom)),None)
        if not school:continue
        title=card.xpath('.//h4')
        a,b=prop('startDate'),prop('endDate')
        if not a or not b or not title:raise ValueError('MLH event fields changed')
        dt.date.fromisoformat(a[:10]);dt.date.fromisoformat(b[:10])
        # MLH timestamps include apparent placeholders (01:11:11Z): use published dates,
        # not converted local instants, for day-level discovery/reminders.
        out.append(event(school,'mlh',text(title[0]),url,a[:10],b[:10],a+' — '+b,evidence_url=source_url,date_precision='day',raw_start=a,raw_end=b))
    return out

def organizer_parse(d,source,url):
    title=clean(' '.join(d.xpath('//head/title/text()')))
    if source=='uofthacks':
        labels=[text(x) for x in d.xpath('//span') if re.fullmatch(MONTH+r' \d{4} \| In-person event',text(x),re.I)]
        if not labels:raise ValueError('UofTHacks hero month missing; recheck organizer')
        m=re.search(r'('+MONTH+r') (\d{4})',labels[0],re.I)
        return [event('U of T',source,title,url,raw_date=labels[0],date_precision='month',announced_month=f'{m[2]}-{MONTHS[m[1].lower()]:02d}',note='Hero is month-only; FAQ contained a stale 2026 edition at audit. Exact dates require another source.')]
    selector='//p' if source=='deltahacks' else '//main//span'
    labels=[text(x) for x in d.xpath(selector) if RANGE.search(text(x))]
    if not labels:raise ValueError(f'{source}: hero event date missing')
    label=labels[0];a,b=dates(label)
    return [event('McMaster' if source=='deltahacks' else 'Carleton',source,title,url,a,b,label,date_precision='day')]

PROVIDERS={
 'tmu':lambda c,a:tmu(c),
 'waterloo':lambda c,a:listing(c,'Waterloo','waterloo','https://uwaterloo.ca/events/events',a.max_pages),
 'uottawa':lambda c,a:listing(c,'uOttawa','uottawa','https://www.uottawa.ca/faculty-engineering/events-all',a.max_pages),
 'carleton':lambda c,a:carleton(c),
 'carleton_scs':lambda c,a:carleton_parse(c.json('https://carleton.ca/scs/wp-json/cutheme/v1/cu-calendar'),'carleton_scs'),
 'uofthacks':lambda c,a:organizer_parse(c.page('https://uofthacks.com/'),'uofthacks','https://uofthacks.com/'),
 'deltahacks':lambda c,a:organizer_parse(c.page('https://www.deltahacks.com/'),'deltahacks','https://www.deltahacks.com/'),
 'cuhacking':lambda c,a:organizer_parse(c.page('https://cuhacking.ca/'),'cuhacking','https://cuhacking.ca/'),
 'mlh':lambda c,a:mlh_parse(c.page(f'https://www.mlh.com/seasons/{a.season}/events'),f'https://www.mlh.com/seasons/{a.season}/events'),
}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sources',nargs='+',choices=PROVIDERS,default=list(PROVIDERS))
    p.add_argument('--as-of',type=dt.date.fromisoformat,default=dt.datetime.now(TZ).date())
    p.add_argument('--season',type=int,default=2027,help='MLH season, not calendar year; update annually')
    p.add_argument('--max-pages',type=int,default=40)
    p.add_argument('--include-past',action='store_true')
    p.add_argument('--all-events',action='store_true',help='Diagnostic: include non-hackathon campus events')
    a=p.parse_args();c=Client();statuses=[];events=[]
    for source in a.sources:
        try:
            rows=PROVIDERS[source](c,a); selected=[]
            for e in rows:
                if source in ['tmu','waterloo','uottawa','carleton','carleton_scs'] and not a.all_events and not HACK.search(e['title']+' '+e['description']):continue
                start=e['start_date'];end=e['end_date'] or start
                if end and end<a.as_of.isoformat():state='past'
                elif start and start>a.as_of.isoformat():state='upcoming'
                elif start and end:state='ongoing_or_today'
                else:state='needs_date_review'
                e['status']=state
                if state!='past' or a.include_past:selected.append(e)
            events.extend(selected)
            statuses.append(dict(source=source,status='SUCCEEDED',records_scanned=len(rows),records_returned=len(selected)))
            print(f'SUCCEEDED: {source}: {len(rows)} scanned, {len(selected)} returned',file=sys.stderr)
        except Exception as e:
            statuses.append(dict(source=source,status='FAILED',error=str(e)))
            print(f'FAILED: {source}: {e}',file=sys.stderr)
    # Preserve conflicting source assertions; only remove exact within-source duplicates.
    unique={(e['source'],e['url'],e['start_date'],e['end_date']):e for e in events}
    result=dict(checked_at=dt.datetime.now(dt.timezone.utc).isoformat(),as_of=a.as_of.isoformat(),timezone='America/Toronto',sources=statuses,events=list(unique.values()),requests=c.log)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 1 if any(x['status']=='FAILED' for x in statuses) else 0

if __name__=='__main__':sys.exit(main())

#!/usr/bin/env python3
"""Best-effort RH NBA half/quarter market snapshots.

Only public event pages, no account/login. Public contract-selection prices
are stored as indicative displayPrice distinct from verified yesAsk/noAsk.
Unavailable markets remain links without fabricated quotes.
"""
import concurrent.futures
import json
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'period_markets.json'
BASE = 'https://robinhood.com/us/en/prediction-markets/mens-pro-basketball/events/'
PT = ZoneInfo('America/Los_Angeles')
CITY = {
 'ATL':'atlanta','BOS':'boston','BKN':'brooklyn','CHA':'charlotte','CHI':'chicago',
 'CLE':'cleveland','DAL':'dallas','DEN':'denver','DET':'detroit','GSW':'golden-state',
 'HOU':'houston','IND':'indiana','LAC':'la-clippers','LAL':'los-angeles',
 'MEM':'memphis','MIA':'miami','MIL':'milwaukee','MIN':'minnesota',
 'NOP':'new-orleans','NYK':'new-york','OKC':'oklahoma-city','ORL':'orlando',
 'PHI':'philadelphia','PHX':'phoenix','POR':'portland','SAC':'sacramento',
 'SAS':'san-antonio','TOR':'toronto','UTA':'utah','WAS':'washington'
}
# Supplied RH examples verify MIL vs BOS / POR vs PHX naming; others must pass page checks.
PERIODS = {
 'h1':'1st-half','h2':'2nd-half',
 'q1':'1st-quarter','q2':'2nd-quarter','q3':'3rd-quarter','q4':'4th-quarter'
}
MARKETS = ('winner','spread','total')

class NextData(HTMLParser):
 def __init__(self):
  super().__init__(); self.in_next=False; self.in_title=False; self.ignore=0; self.next=[]; self.title=[]; self.visible=[]
 def handle_starttag(self,tag,attrs):
  attrs=dict(attrs)
  if tag=='script' and attrs.get('id')=='__NEXT_DATA__':self.in_next=True
  if tag=='title':self.in_title=True
  if tag in ('script','style','noscript'):self.ignore+=1
 def handle_endtag(self,tag):
  if tag=='script':self.in_next=False
  if tag=='title':self.in_title=False
  if tag in ('script','style','noscript'):self.ignore=max(self.ignore-1,0)
 def handle_data(self,data):
  if self.in_next:self.next.append(data)
  if self.in_title:self.title.append(data)
  if not self.ignore and data.strip():self.visible.append(unescape(data.strip()))

def fetch(url):
 req=Request(url, headers={'User-Agent':'Mozilla/5.0 (compatible; sports-lottery/1.0)', 'Accept':'text/html'})
 with urlopen(req,timeout=13) as res:
  if res.status!=200:raise ValueError('HTTP '+str(res.status))
  raw=res.read(2_000_000).decode('utf-8','replace')
 return raw

def walk(obj, depth=0):
 if depth>14:return
 if isinstance(obj,dict):
  yield obj
  for v in obj.values():yield from walk(v,depth+1)
 elif isinstance(obj,list):
  for v in obj:yield from walk(v,depth+1)

def price(q,field):
 value=q.get(field)
 if value is None:return None
 try:
  v=float(value)
  if 0<v<1:return round(v*100,4)
 except (TypeError,ValueError):pass
 return None

# These mappings only identify a contract label already present in the event page;
# they never turn a headline/last-trade price into an ask.
NAMES = {
 'ATL':('Atlanta','Hawks'),'BOS':('Boston','Celtics'),'BKN':('Brooklyn','Nets'),
 'CHA':('Charlotte','Hornets'),'CHI':('Chicago','Bulls'),'CLE':('Cleveland','Cavaliers'),
 'DAL':('Dallas','Mavericks'),'DEN':('Denver','Nuggets'),'DET':('Detroit','Pistons'),
 'GSW':('Golden State','Warriors'),'HOU':('Houston','Rockets'),'IND':('Indiana','Pacers'),
 'LAC':('LA Clippers','Clippers'),'LAL':('LA Lakers','Lakers','Los Angeles'),
 'MEM':('Memphis','Grizzlies'),'MIA':('Miami','Heat'),'MIL':('Milwaukee','Bucks'),
 'MIN':('Minnesota','Timberwolves'),'NOP':('New Orleans','Pelicans'),
 'NYK':('New York','Knicks'),'OKC':('Oklahoma City','Thunder'),
 'ORL':('Orlando','Magic'),'PHI':('Philadelphia','76ers'),'PHX':('Phoenix','Suns'),
 'POR':('Portland','Trail Blazers'),'SAC':('Sacramento','Kings'),
 'SAS':('San Antonio','Spurs'),'TOR':('Toronto','Raptors'),
 'UTA':('Utah','Jazz'),'WAS':('Washington','Wizards')
}

def explicit_price(v):
 """Read explicit cent, decimal, or fractional ask; reject absent/ambiguous values."""
 if isinstance(v,bool) or v is None:return None
 try:
  if isinstance(v,str) and ('¢' in v or 'cent' in v.lower()):
   x=float(re.search(r'\d+(?:\.\d+)?',v).group(0))
  else:
   x=float(v)
   if 0<x<1:x*=100
  return round(x,4) if 0<x<100 else None
 except (TypeError,ValueError,AttributeError):return None

def label_owner(label,away,home):
 x=re.sub(r'[^a-z0-9]','',label.lower())
 if x in ('tie','draw','tied'):return 'tie'
 for code in (away,home):
  for name in NAMES[code]:
   n=re.sub(r'[^a-z0-9]','',name.lower())
   if x==n:return code
 return None

def parse_explicit_visible_ask(parser,away,home,kind):
 """Conservative fallback: use only the actually selected RH contract's Bid/Ask.

 The headline 51¢/46¢/4¢ is NOT necessarily the executable ask. If RH
 doesn't server-render the selected contract and its ask, this stays empty.
 """
 if kind!='winner':return []
 t=parser.visible
 text=' '.join(t)
 # RH's selected trade ticket appears after the contract terms. We require
 # a labeled bid and ask, not price snippets from the market summary.
 pattern=re.compile(r'\bBid\s*(\d+(?:\.\d+)?)\s*¢\s*[·•|/]?\s*Ask\s*(\d+(?:\.\d+)?)\s*¢',re.I)
 quotes=list(pattern.finditer(text))
 if len(quotes)!=1:return []   # avoid selecting a wrong ticket if ambiguous
 m=quotes[0]
 # Determine ticket's selected team from the nearest visible known label
 # *after* the page's market summary / legal text, just before Bid/Ask.
 segment=text[max(0,m.start()-1000):m.start()]
 hits=[]
 for code in (away,home):
  for name in NAMES[code]:
   for found in re.finditer(r'(?<!\w)'+re.escape(name)+r'(?!\w)',segment,re.I):hits.append((found.start(),code,name))
 if not hits:return []
 hits.sort(reverse=True)
 # Only publish when nearest recognizable label is in the ticket vicinity.
 pos,code,label=hits[0]
 if len(segment)-pos>350:return []
 ask=explicit_price(m.group(2))
 return [{'id':'visible-ticket-'+code,'label':label,'yesAsk':ask,'noAsk':None}] if ask else []

def parse_visible_contract_choices(parser, away, home, kind):
 """Read all publicly displayed contract choices for winner/spread/total.

 displayPrice is a reference quote, NOT independently verified executable ask.
 The client may use it for an explicitly-labelled estimated return.
 """
 parts=[str(x).strip() for x in parser.visible if str(x).strip()]
 start=next((i for i,x in enumerate(parts) if x.lower()=='select a contract'),None)
 if start is None:return []
 end=next((i for i in range(start+1,len(parts)) if parts[i].lower() in ('timeline','about','trading hours','similar events')),len(parts))
 sub=parts[start+1:end]
 choices=[]
 for i in range(len(sub)-1):
  label=sub[i]
  owner=label_owner(label,away,home) if kind=='winner' else None
  if kind=='winner' and owner is None:continue
  if kind=='spread':
   # RH often uses 'Boston wins by over 5.5 points'; line belongs to Boston.
   m=re.fullmatch(r'(.+?)\s+wins\s+by\s+(?:over|more than)\s+(\d+(?:\.\d+)?)\s+points?',label,re.I)
   if not m:continue
   owner=label_owner(m.group(1),away,home)
   if owner not in (away,home):continue
  if kind=='total':
   m=re.fullmatch(r'(over|under)\s+(\d+(?:\.\d+)?)\s+points?',label,re.I)
   if not m:continue
   owner=m.group(1).lower()
  price_match=re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*¢\s*',sub[i+1])
  if not price_match:continue
  cents=explicit_price(price_match.group(1)+'¢')
  if cents is None:continue
  # Stable semantic ID: different spread lines or totals remain distinct.
  cid= ('display-'+owner if kind=='winner' else
        'display-'+kind+'-'+owner+'-'+(m.group(2).replace('.','p')))
  if cid not in [x['id'] for x in choices]:
   choices.append({'id':cid,'owner':owner,'label':label,
                   'displayPrice':cents,'yesAsk':None,'noAsk':None,
                   'priceSource':'RH public event selection (indicative)'})
 return choices

def combine_display_with_ask(displayed, verified, away,home):
 """Only merge an actual selected-ticket ask into its matching contract."""
 result=[dict(item) for item in displayed]
 for quote in verified:
  owner=label_owner(str(quote.get('label','')),away,home)
  if owner is None:continue
  match=next((x for x in result if x.get('owner')==owner),None)
  if match:
   if quote.get('yesAsk') is not None:match['yesAsk']=quote['yesAsk']
   if quote.get('noAsk') is not None:match['noAsk']=quote['noAsk']
   if str(quote.get('id','')).startswith('visible-ticket-'):
    match['askVerifiedFrom']='ticket'
  else:
   result.append({**quote,'owner':owner,'displayPrice':None})
 return result

def parse_market(html,away,home,period,kind,url,day):
 parser=NextData();parser.feed(html)
 page_title=''.join(parser.title).lower()
 title=page_title.replace('-',' ')
 if not all(token in title for token in (PERIODS[period].replace('-',' '),kind)):
  return {'url':url,'choices':[],'reason':'RH event title differs from requested period/market'}
 # Do not parse an unrelated redirected event.
 if not all(any(n.lower() in title for n in NAMES[code]) for code in (away,home)):
  return {'url':url,'choices':[],'reason':'RH event teams do not match'}
 displayed=parse_visible_contract_choices(parser,away,home,kind)
 fallback=combine_display_with_ask(displayed,parse_explicit_visible_ask(parser,away,home,kind),away,home)
 if not parser.next:
  return {'url':url,'choices':fallback,'reason':None if fallback else 'No __NEXT_DATA__ and no explicit ticket ask in HTML'}
 try:root=json.loads(''.join(parser.next))
 except (ValueError,TypeError):
  return {'url':url,'choices':fallback,'reason':None if fallback else 'Invalid __NEXT_DATA__'}
 contracts=[]; quote_by_id={}; contract_sets=set()
 ask_keys=(('yes_ask_price','yesAskPrice','yes_ask','yesAsk'),('no_ask_price','noAskPrice','no_ask','noAsk'))
 def getask(obj,keys):
  for k in keys:
   if k in obj:
    value=explicit_price(obj[k])
    if value is not None:return value
  return None
 def hasask(obj):return any(getask(obj,keys) is not None for keys in ask_keys)
 for obj in walk(root):
  for k in ('quotes','contractQuotes','marketQuotes'):
   coll=obj.get(k)
   if isinstance(coll,dict):
    for cid,q in coll.items():
     if isinstance(q,dict) and hasask(q):quote_by_id[str(cid)]=q
   elif isinstance(coll,list):
    for q in coll:
     if isinstance(q,dict):
      cid=q.get('contractId') or q.get('contract_id') or q.get('id')
      if cid and hasask(q):quote_by_id[str(cid)]=q
  coll=obj.get('eventContracts')
  if isinstance(coll,dict) and coll:
   contract_sets.add(frozenset(str(x.get('id') or k) for k,x in coll.items() if isinstance(x,dict)))
   contracts.extend({**x,'id':x.get('id') or k} for k,x in coll.items() if isinstance(x,dict))
  elif isinstance(coll,list) and coll:
   contract_sets.add(frozenset(str(x.get('id')) for x in coll if isinstance(x,dict) and x.get('id')))
   contracts.extend(x for x in coll if isinstance(x,dict))
  if hasask(obj):
   cid=obj.get('contractId') or obj.get('contract_id') or obj.get('id')
   if cid:quote_by_id[str(cid)]=obj
 # Several embedded events can appear in related-events widgets: don't
 # combine their quotes with the main selected event.
 if len({v for v in contract_sets if v})>1:
  return {'url':url,'choices':fallback,'reason':None if fallback else 'Ambiguous embedded event contract groups'}
 unique={str(c.get('id')):c for c in contracts if c.get('id')}
 choices=[]
 for cid,c in unique.items():
  q=quote_by_id.get(cid,{})
  yes,no=getask(q,ask_keys[0]),getask(q,ask_keys[1])
  if yes is None and no is None:continue
  label=str(c.get('displayName') or c.get('name') or c.get('displayShortName') or c.get('title') or c.get('displayText') or c.get('subtitle') or '').strip()
  if not label:continue
  choices.append({'id':cid,'label':label,'yesAsk':yes,'noAsk':no})
 verified=combine_display_with_ask(displayed,choices or parse_explicit_visible_ask(parser,away,home,kind),away,home)
 return {'url':url,'choices':verified,'reason':None if verified else 'No contract selections or explicit asks found in HTML/Next.js'}

def urls(away,home,period,kind,day):
 date=datetime.strptime(day,'%Y-%m-%d').strftime('%b-%d-%Y').lower()
 # RH distinguishes Lakers/Clippers on some slugs; verify response before use.
 # Return only the canonical candidate; missing pages become unavailable.
 return f'{BASE}{CITY[away]}-vs-{CITY[home]}-{PERIODS[period]}-{kind}-{date}/'

def fetch_market(task):
 game_id,day,away,home,period,kind=task
 url=urls(away,home,period,kind,day)
 try:return game_id,period,kind,parse_market(fetch(url),away,home,period,kind,url,day)
 except (HTTPError,URLError,OSError,ValueError,KeyError,TypeError) as ex:
  return game_id,period,kind,{'url':None if isinstance(ex,HTTPError) and ex.code==404 else url,'choices':[],'reason':str(ex)[:100]}

def build(games,day,fetcher=fetch_market):
 result={g['id']:{'id':g['id'],'markets':{p:{} for p in PERIODS}} for g in games}
 jobs=[]
 for g in games:
  away=g['away']['code'];home=g['home']['code']
  if away not in CITY or home not in CITY:continue
  for p in PERIODS:
   for market in MARKETS:jobs.append((g['id'],day,away,home,p,market))
 # Rate friendly: a few concurrent requests, independent failures.
 with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
  for gid,p,market,data in pool.map(fetcher,jobs):result[gid]['markets'][p][market]=data
 return list(result.values())

def main():
 source=json.loads((ROOT/'odds.json').read_text(encoding='utf-8'))
 day=source.get('date');games=source.get('games',[])
 if not isinstance(day,str) or not games:raise RuntimeError('No daily NBA slate in odds.json')
 previous={}
 if DEST.exists():
  try:previous=json.loads(DEST.read_text(encoding='utf-8'))
  except (OSError,ValueError):pass
 # Avoid 270 requests on every 5-minute odds poll. Refresh at most every 2h,
 # and immediately when the slate date changes. Manual refresh --force allowed.
 if '--force' not in sys.argv and previous.get('date')==day:
  try:
   last=datetime.fromisoformat(previous['capturedAt'])
   if (datetime.now(timezone.utc)-last).total_seconds()<7200:
    print('Period markets: cached < 2h; skipping network scan');return
  except (KeyError,TypeError,ValueError):pass
 print(f'Checking up to {len(games)*18} RH period event pages for {day}',flush=True)
 records=build(games,day)
 has_asks=sum(sum(1 for x in m.get('choices',[]) if x.get('yesAsk') is not None or x.get('noAsk') is not None) for g in records for p in g['markets'].values() for m in p.values())
 displayed_count=sum(sum(1 for x in m.get('choices',[]) if x.get('displayPrice') is not None) for g in records for p in g['markets'].values() for m in p.values())
 from collections import Counter
 reasons=Counter(m.get('reason') or 'OK' for g in records for p in g['markets'].values() for m in p.values())
 print('Period market fetch diagnostics:', dict(reasons.most_common(12)), 'displayed selections:',displayed_count,'verified asks:',has_asks,flush=True)
 for game in records:
  sample=game['markets'].get('h1',{}).get('winner',{})
  print('H1 winner diagnostic:',game['id'],'URL:',sample.get('url'),'choices:',len(sample.get('choices',[])),'reason:',sample.get('reason'),flush=True)
  if sample.get('choices'):
   print('H1 winner labels/asks:',[(x.get('label'),x.get('yesAsk')) for x in sample['choices']],flush=True)
 for game in records[:2]:
  for key in ('h1','h2'):
   r=game['markets'][key].get('winner',{})
   print('Sample',game['id'],key,'winner:',r.get('reason') or f"{len(r.get('choices',[]))} ask choices",flush=True)
 payload={'date':day,'capturedAt':datetime.now(timezone.utc).isoformat(timespec='seconds'),'source':'RH public event pages; displayPrice is INDICATIVE, yesAsk/noAsk only when verified','games':records}
 # A total source failure should not overwrite a healthy SAME-DAY cache.
 if not has_asks and not displayed_count and previous.get('date')==day and sum(len(m.get('choices',[])) for g in previous.get('games',[]) for p in g.get('markets',{}).values() for m in p.values()):
  print('Warning: no RH ask prices across all requested pages; keeping existing cache unchanged',flush=True)
  return
 DEST.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(f'Period markets: {len(records)} games, {has_asks} verified buy-side asks, {displayed_count} indicative choice quotes, saved {DEST.name}',flush=True)

if __name__=='__main__':main()

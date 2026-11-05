#!/usr/bin/env python3
"""Diagnostic only. Fetch public RH NBA HTML and explain event inclusion/exclusion.
Does NOT modify any caches. Run in GitHub Actions (workflow_dispatch).
"""
import json
import re
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

URL = 'https://robinhood.com/us/en/prediction-markets/nba/games/'
PT = ZoneInfo('America/Los_Angeles')

class NextData(HTMLParser):
    def __init__(self):
        super().__init__(); self.active=False; self.parts=[]
    def handle_starttag(self, tag, attrs):
        if tag=='script' and dict(attrs).get('id')=='__NEXT_DATA__': self.active=True
    def handle_endtag(self, tag):
        if tag=='script': self.active=False
    def handle_data(self, data):
        if self.active: self.parts.append(data)

req=Request(URL,headers={'User-Agent':'Mozilla/5.0','Accept':'text/html','Cache-Control':'no-cache'})
with urlopen(req,timeout=40) as response:
    html=response.read().decode('utf-8')
p=NextData();p.feed(html)
if not p.parts:
    raise RuntimeError(f'__NEXT_DATA__ unavailable (HTML bytes={len(html)})')
props=json.loads(''.join(p.parts))['props']['pageProps']
events=props.get('events',[]);states=props.get('eventStates',{});quotes=props.get('quotes',{})
print('Fetched UTC:',datetime.now(timezone.utc).isoformat())
print('HTML bytes:',len(html),'events:',len(events),'states:',len(states),'quotes:',len(quotes))
print('Event status overview:',dict(Counter(str(s.get('eventStatus')) for s in states.values())))
print('\nMATCHED NBA GAME EVENTS (all known dates):')
found=[]
for event in events:
    state=states.get(event.get('id'),{})
    start=state.get('gameStart')
    cs=list((event.get('eventContracts') or {}).values())
    gamecs=[c for c in cs if re.search(r'NBAGAME-',str(c.get('symbol','')))]
    if not (start or gamecs): continue
    if not (event.get('isSports') or gamecs): continue
    local='NO GAME START'
    if start:
        try: local=datetime.fromisoformat(start.replace('Z','+00:00')).astimezone(PT).isoformat()
        except ValueError:local=f'BAD DATE: {start}'
    filtered=[c for c in cs if c.get('isTeamContract') and not c.get('isCombo')]
    reasons=[]
    if not start: reasons.append('MISSING gameStart')
    if not event.get('isSports'):reasons.append('isSports not true')
    if len(filtered)!=2:reasons.append(f'{len(filtered)} team contracts, expected 2')
    short_names=[str(c.get('displayShortName','')) for c in filtered]
    yes_quotes=sum(quotes.get(c.get('id'),{}).get('yes_ask_price') is not None for c in filtered)
    print(f"- {event.get('name')} | startPT={local} | status={state.get('eventStatus')} | progress={str(state.get('eventProgress'))[:70]} | contracts={len(cs)} teamContracts={len(filtered)} yesPrices={yes_quotes} | {', '.join(short_names)} | skipReasons={'; '.join(reasons) or 'none'}")
    found.append((local, event.get('name'),reasons))
print('\nFound:',len(found),'NBA-like events')
print('Potentially excluded:',sum(bool(x[2]) for x in found))
print('NOTE: If live games are absent entirely, the initial NBA page omits those events; compare with the public RH live listing. If present but skipReasons appear, adapt the parser to live structure.')

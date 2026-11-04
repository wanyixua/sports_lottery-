#!/usr/bin/env python3
"""Diagnostic: count NBA games and quotes from Robinhood's Next.js public page.
No repository files are modified. Public interface may change or block automation.
Usage: python check_rh_nba.py [local-html-file] [YYYY-MM-DD]
If no HTML is supplied, fetches the public NBA games page.
"""
import json
import re
import sys
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

URL = 'https://robinhood.com/us/en/prediction-markets/nba/games/'
class NextData(HTMLParser):
    def __init__(self):
        super().__init__(); self.active=False; self.chunks=[]
    def handle_starttag(self, tag, attrs):
        if tag=='script' and dict(attrs).get('id')=='__NEXT_DATA__': self.active=True
    def handle_endtag(self, tag):
        if tag=='script': self.active=False
    def handle_data(self, data):
        if self.active: self.chunks.append(data)

def extract(html):
    parser=NextData(); parser.feed(html)
    if not parser.chunks: raise ValueError('No __NEXT_DATA__ found (page changed or JS only)')
    p=json.loads(''.join(parser.chunks))['props']['pageProps']
    return p['events'],p['eventStates'],p['quotes']

def main():
    args=sys.argv[1:]
    if args and Path(args[0]).is_file():
        html=Path(args.pop(0)).read_text(encoding='utf-8')
    else:
        req=Request(URL,headers={'User-Agent':'Mozilla/5.0 (compatible; NBAOddsDiagnostic/1.0)','Accept':'text/html'})
        with urlopen(req, timeout=25) as r: html=r.read().decode('utf-8')
    day=args[0] if args else datetime.now(ZoneInfo('America/Los_Angeles')).date().isoformat()
    events,states,quotes=extract(html)
    games=[]
    for event in events:
        state=states.get(event['id'],{})
        start=state.get('gameStart')
        if not start: continue
        local=datetime.fromisoformat(start.replace('Z','+00:00')).astimezone(ZoneInfo('America/Los_Angeles'))
        if local.date().isoformat()!=day: continue
        contracts=[c for c in event.get('eventContracts',{}).values() if c.get('isTeamContract') and not c.get('isCombo')]
        if len(contracts)!=2: continue
        row=[]
        for c in contracts:
            q=quotes.get(c['id'],{})
            price=q.get('yes_ask_price')
            row.append((c.get('displayShortName'),price))
        games.append((local,row))
    games.sort(key=lambda x:x[0])
    valid=sum(all(p is not None for _,p in row) for _,row in games)
    print(f'{day}: {len(games)} two-team games; {valid} with both ask quotes; {sum(sum(p is not None for _,p in row) for _,row in games)} quoted contracts')
    for dt,row in games:
        print(dt.strftime('%H:%M'), ' vs '.join(f'{team}: {price if price is not None else "MISSING"}' for team,price in row))
    if not games or valid != len(games): sys.exit(2)

if __name__=='__main__': main()

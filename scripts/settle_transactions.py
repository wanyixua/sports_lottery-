#!/usr/bin/env python3
"""Safely settle NBA transaction legs against ESPN final scores.

Each leg may specify gameDate (YYYY-MM-DD, US NBA calendar date).
If absent, retains old behavior and evaluates on the order's usDate.
Never use a different day's game without an explicit per-leg gameDate.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ESPN_URL = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={date}&limit=100'
TEAMS = set('ATL BOS BKN CHA CHI CLE DAL DEN DET GSW HOU IND LAC LAL MEM MIA MIL MIN NOP NYK OKC ORL PHI PHX POR SAC SAS TOR UTA WAS'.split())
ALIASES = {'NY':'NYK','GS':'GSW','SA':'SAS','NO':'NOP','UTAH':'UTA','WSH':'WAS','PHO':'PHX','BRK':'BKN'}
LEG_RE = re.compile(r'\b([A-Z]{2,4})(?:\s*([+-])\s*(\d+(?:\.\d+)?))?\b')


def parse_legs(bet):
    legs=[]
    for m in LEG_RE.finditer(bet.upper()):
        team=ALIASES.get(m.group(1),m.group(1))
        if team not in TEAMS: raise ValueError(f'Unknown team {m.group(1)!r} in {bet!r}')
        line=float(m.group(3))*(1 if m.group(2)=='+' else -1) if m.group(3) else None
        legs.append({'team':team,'market':'spread' if line is not None else 'moneyline', **({'line':line} if line is not None else {})})
    if not legs or len({x['team'] for x in legs})!=len(legs):
        raise ValueError(f'Cannot safely parse legs: {bet!r}')
    return legs


def is_final(comp):
    typ=(comp.get('status') or {}).get('type') or {}
    return typ.get('completed') is True and typ.get('name') == 'STATUS_FINAL'


def scoreboard_games(payload, required_date):
    result={}
    for event in payload.get('events',[]):
        for comp in event.get('competitions',[]):
            if not is_final(comp): continue
            if not (event.get('date') or comp.get('date')): continue
            teams={}
            for contender in comp.get('competitors',[]):
                code=ALIASES.get(str(contender.get('team',{}).get('abbreviation','')).upper(), str(contender.get('team',{}).get('abbreviation','')).upper())
                try: score=int(float(contender.get('score')))
                except (ValueError,TypeError): continue
                if code in TEAMS: teams[code]=score
            if len(teams)==2:
                for code,score in teams.items():
                    opp,opp_score=next((k,v) for k,v in teams.items() if k!=code)
                    if code in result: raise ValueError(f'Duplicate final game for {code} on {required_date}')
                    result[code]={'score':score,'opponent':opp,'oppScore':opp_score,'eventId':event.get('id'),'date':required_date,'final':True}
    return result


def grade_leg(leg,games):
    game=games.get(leg['team'])
    if not game:return None
    margin=game['score']-game['oppScore']
    if leg['market']=='moneyline': return 'win' if margin>0 else ('loss' if margin<0 else 'push')
    if leg['market']=='spread':
        adjusted=margin+leg['line']
        return 'win' if adjusted>0 else ('loss' if adjusted<0 else 'push')
    if leg['market']=='total':
        points=game['score']+game['oppScore']
        adjusted=points-leg['line'] if leg['side']=='over' else leg['line']-points
        return 'win' if adjusted>0 else ('loss' if adjusted<0 else 'push')
    raise ValueError(f'Unknown market: {leg}')


def leg_date(leg,record):
    day=leg.get('gameDate') or record['usDate']
    if not isinstance(day,str) or dt.date.fromisoformat(day).isoformat()!=day:
        raise ValueError(f'Invalid gameDate {day!r} for order #{record.get("id")}')
    return day


def grade_record(record,games):
    """Accept {date:{team:game}}; also accepts legacy single-date team map."""
    legs=record.get('legs') or parse_legs(record['bet'])
    dated=bool(games) and all(isinstance(v,dict) and 'score' not in v for v in games.values())
    results=[grade_leg(leg, games.get(leg_date(leg,record),{}) if dated else games) for leg in legs]
    if 'push' in results:return 'review',results
    if 'loss' in results:return 'loss',results
    if all(x=='win' for x in results):return 'win',results
    return 'pending',results


def fetch_scoreboard(day,fixtures=None,retries=3):
    if fixtures is not None:
        fixture=fixtures/f'{day}.json'
        if not fixture.exists():return {'events':[]}
        return json.loads(fixture.read_text(encoding='utf-8'))
    for attempt in range(retries):
        try:
            req=urllib.request.Request(ESPN_URL.format(date=day.replace('-','')), headers={'User-Agent':'Mozilla/5.0 (compatible; XiaowangLedger/1.0)','Accept':'application/json'})
            with urllib.request.urlopen(req,timeout=25) as resp:return json.load(resp)
        except Exception:
            if attempt==retries-1:raise
            time.sleep(2*(attempt+1))


def update(db,fixtures=None):
    pending=[r for r in db['records'] if r.get('status')=='pending']
    dates=sorted({leg_date(leg,r) for r in pending for leg in (r.get('legs') or parse_legs(r['bet']))})
    games_by_date={}
    for day in dates:
        games_by_date[day]=scoreboard_games(fetch_scoreboard(day,fixtures),day)
        print(f'{day}: {len(games_by_date[day])//2} final games found')
    modified=0
    for r in pending:
        try:
            legs=r.get('legs') or parse_legs(r['bet'])
            if not r.get('legs'):r['legs']=legs;modified+=1
            new_status,results=grade_record(r,games_by_date)
        except (ValueError,KeyError,TypeError) as exc:
            print(f'REVIEW #{r.get("id")}: {exc}',file=sys.stderr);continue
        if new_status=='pending':
            print(f'PENDING #{r["id"]} {r["bet"]}: {results}');continue
        if new_status=='review':
            r['status']='review';r['settlementNote']='Push / void requires RH verification';modified+=1;continue
        r['legResults']=results
        r['resultSource']='ESPN scoreboard (final scores by each leg gameDate); verify special RH settlement rules'
        if new_status=='loss':
            r['status']='loss';r['payout']=0
        else:
            stake,net=r.get('stake'),r.get('profitIfWin')
            if stake is None or net is None:
                r['status']='win_needs_amount';r['payout']=None
            else:
                r['status']='win';r['payout']=round(float(stake)+float(net),2)
        modified+=1
        print(f'SETTLED #{r["id"]} {r["bet"]}: {r["status"]}, payout={r["payout"]}')
    return modified


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--file',default='transactions.json')
    p.add_argument('--fixtures',type=Path)
    p.add_argument('--dry-run',action='store_true')
    args=p.parse_args()
    path=Path(args.file)
    db=json.loads(path.read_text(encoding='utf-8'))
    changed=update(db,args.fixtures)
    if changed and not args.dry_run:
        path.write_text(json.dumps(db,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'Updated records: {changed}, dry_run={args.dry_run}')

if __name__=='__main__':main()

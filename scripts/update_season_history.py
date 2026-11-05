#!/usr/bin/env python3
"""Cache completed NBA regular-season results for the website.

Uses ESPN's undocumented public scoreboard endpoint. Fail closed: if any
month cannot be fetched, leave the existing JSON unchanged.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

PT = ZoneInfo('America/Los_Angeles')
OUT = Path(__file__).resolve().parents[1] / 'season_history.json'
URL = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard'


def months(start, end):
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield f'{year}{month:02d}'
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def fetch_month(ym):
    url = URL + '?' + urlencode({'dates': ym, 'limit': '500', 'seasontype': '2'})
    req = Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
    with urlopen(req, timeout=35) as response:
        data = json.load(response)
    if not isinstance(data.get('events'), list):
        raise ValueError(f'Invalid ESPN events for {ym}')
    return data['events']


def parse_event(event, first_day, now):
    comp = next(iter(event.get('competitions', [])), None)
    if not comp:
        return None
    season_type = (event.get('season') or {}).get('type')
    if season_type is not None and str(season_type) != '2':
        return None
    status = (comp.get('status') or event.get('status') or {}).get('type') or {}
    if status.get('completed') is not True or status.get('state') != 'post':
        return None
    event_time = datetime.fromisoformat(event['date'].replace('Z', '+00:00'))
    if not first_day <= event_time.astimezone(PT).date() <= now.date():
        return None
    if event_time > now:
        return None
    competitors = comp.get('competitors') or []
    if len(competitors) != 2:
        return None
    by_side = {c.get('homeAway'): c for c in competitors}
    if set(by_side) != {'home', 'away'}:
        return None
    a, h = by_side['away'], by_side['home']
    away, home = a.get('team', {}).get('abbreviation', '').upper(), h.get('team', {}).get('abbreviation', '').upper()
    if not away or not home or away == home:
        return None
    sa, sh = int(a['score']), int(h['score'])
    if sa == sh:
        return None
    return {'id': str(event.get('id')), 'date': event_time.astimezone(PT).date().isoformat(),
            'away': away, 'home': home, 'awayScore': sa, 'homeScore': sh,
            'winner': away if sa > sh else home}


def build(events_by_month, now):
    pt_today = now.astimezone(PT).date()
    start_year = pt_today.year if pt_today.month >= 7 else pt_today.year - 1
    first_day = datetime(start_year, 10, 1, tzinfo=PT).date()
    unique = {}
    for events in events_by_month:
        for e in events:
            item = parse_event(e, first_day, now)
            if item:
                unique[item['id']] = item
    games = sorted(unique.values(), key=lambda x: (x['date'], x['id']), reverse=True)
    teams = {}
    for g in games:
        for side, opponent in [(g['away'], g['home']), (g['home'], g['away'])]:
            rec = teams.setdefault(side, {'wins': 0, 'losses': 0, 'games': []})
            did_win = side == g['winner']
            rec['wins' if did_win else 'losses'] += 1
            rec['games'].append({'id': g['id'], 'date': g['date'], 'opponent': opponent,
                                 'home': side == g['home'], 'score': (g['homeScore'] if side == g['home'] else g['awayScore']),
                                 'opponentScore': (g['awayScore'] if side == g['home'] else g['homeScore']),
                                 'result': 'W' if did_win else 'L'})
    return {'season': f'{start_year}-{str(start_year+1)[-2:]}', 'seasonEndYear': start_year+1,
            'source': 'ESPN scoreboard (regular season completed games)', 'capturedAt': now.isoformat(),
            'teams': teams, 'games': games}


def main():
    now = datetime.now(timezone.utc)
    pt_now = now.astimezone(PT)
    start_year = pt_now.year if pt_now.month >= 7 else pt_now.year-1
    start = datetime(start_year, 10, 1, tzinfo=PT).date()
    if pt_now.date() < start:
        raise RuntimeError('Season has not started')
    if OUT.exists() and os.getenv('FORCE_HISTORY') != '1':
        try:
            old = json.loads(OUT.read_text(encoding='utf-8'))
            capture = datetime.fromisoformat(old['capturedAt'])
            if old.get('seasonEndYear') == start_year + 1 and now-capture < timedelta(hours=6):
                print('Season history cache is fresh; skipping ESPN')
                return
        except (KeyError, ValueError, TypeError):
            pass
    batches = [fetch_month(m) for m in months(start, pt_now.date())]
    result = build(batches, now)
    if not result['games']:
        raise RuntimeError('No completed regular-season games found; preserving cache')
    tmp = OUT.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(OUT)
    print(f"Saved {len(result['games'])} regular-season games for {len(result['teams'])} teams")


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Cache completed NBA regular-season results for the website.

Uses ESPN's undocumented public scoreboard endpoint. Fail closed: if any
month cannot be fetched, leave the existing JSON unchanged.
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

PT = ZoneInfo('America/Los_Angeles')
OUT = Path(__file__).resolve().parents[1] / 'season_history.json'
URL = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard'


def date_range(start, end):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def fetch_day(day):
    """Fetch a single ESPN scoreboard date (YYYYMMDD), with short retries.

    ESPN's undocumented scoreboard endpoint does not reliably support month-only
    `dates=YYYYMM` queries, and range queries may also fail sporadically.
    """
    date_string = day.strftime('%Y%m%d')
    url = URL + '?' + urlencode({'dates': date_string, 'limit': '100', 'seasontype': '2'})
    last_error = None
    for attempt in range(3):
        try:
            req = Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; XiaoWangNBA/1.0)',
                'Accept': 'application/json',
            })
            with urlopen(req, timeout=25) as response:
                payload = json.load(response)
            events = payload.get('events')
            if not isinstance(events, list):
                raise ValueError(f'ESPN response for {date_string} has no events list')
            return day, events
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(attempt + 1)
    raise RuntimeError(f'ESPN request failed for {date_string}: {last_error}')


def fetch_days(days):
    """Fail closed when any required day cannot be checked."""
    output = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(fetch_day, d): d for d in days}
        for future in as_completed(futures):
            date_key, events = future.result()
            output[date_key] = events
    return [output[d] for d in sorted(output)]


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
    if not first_day <= event_time.astimezone(PT).date() <= now.astimezone(PT).date():
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
    return build_from_parsed(list(unique.values()), now)


def build_from_parsed(parsed, now):
    pt_today = now.astimezone(PT).date()
    start_year = pt_today.year if pt_today.month >= 7 else pt_today.year - 1
    games = sorted(parsed, key=lambda x: (x['date'], x['id']), reverse=True)
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
    start_year = pt_now.year if pt_now.month >= 7 else pt_now.year - 1
    start = date(start_year, 10, 1)
    if pt_now.date() < start:
        raise RuntimeError('NBA regular season has not started')

    existing = None
    if OUT.exists():
        try:
            existing = json.loads(OUT.read_text(encoding='utf-8'))
            if existing.get('seasonEndYear') != start_year + 1 or not isinstance(existing.get('games'), list):
                existing = None
        except (ValueError, TypeError, KeyError):
            existing = None

    if existing and os.getenv('FORCE_HISTORY') != '1':
        try:
            captured = datetime.fromisoformat(existing['capturedAt'])
            if now - captured < timedelta(hours=6):
                print(f'Season history fresh: {len(existing["games"])} games, skipping')
                return
        except (ValueError, TypeError, KeyError):
            pass

    # ESPN date filters are Eastern-centric; include one additional date on
    # either side to avoid missing games scheduled near midnight.
    if existing and existing['games']:
        latest = max(date.fromisoformat(g['date']) for g in existing['games'])
        query_start = max(start, latest - timedelta(days=3))
        print(f'Incremental season history refresh from {query_start}')
    else:
        query_start = start
        print(f'Initial season history build from {start}')
    days = list(date_range(query_start - timedelta(days=1), pt_now.date() + timedelta(days=1)))
    batches = fetch_days(days)
    print(f'Fetched {len(days)} ESPN scoreboard dates; source events: {sum(map(len, batches))}')

    # Retain settled older games when refreshing recent dates only.
    old_events = []
    if existing:
        for g in existing['games']:
            if date.fromisoformat(g['date']) < query_start:
                old_events.append(g)
    result = build(batches, now)
    if old_events:
        by_id = {g['id']: g for g in old_events}
        by_id.update({g['id']: g for g in result['games']})
        result = build_from_parsed(list(by_id.values()), now)
    if not result['games']:
        raise RuntimeError('ESPN returned no completed regular-season games; existing cache preserved')
    if existing and len(result['games']) < len(existing['games']):
        raise RuntimeError(f'History shrank {len(existing["games"])} -> {len(result["games"])}; preserving existing cache')
    tmp = OUT.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(OUT)
    print(f'Saved {len(result["games"])} regular-season games, {len(result["teams"])} teams to {OUT}')


if __name__ == '__main__':
    main()

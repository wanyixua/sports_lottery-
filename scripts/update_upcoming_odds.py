#!/usr/bin/env python3
"""Publish every NBA matchup returned by RH's publicly rendered game index.

Separate from the current-day caches so today's live/final games are preserved.
Only quote fields actually present in RH's page are published.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

from update_odds import PT, TEAMS, build_games, fetch_page, load_existing_game_times, parse
from spread_extract import spread_groups
from total_extract import total_groups

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'upcoming_markets.json'


def main():
    now = datetime.now(PT)
    events, states, quotes = parse(fetch_page())
    known_times = load_existing_game_times(ROOT / 'odds.json')
    try:
        previous = json.loads(DEST.read_text(encoding='utf-8'))
        known_times.update({g['id']: g['startTimePT'] for g in previous.get('games', [])
                            if g.get('id') and g.get('startTimePT')})
    except (OSError, ValueError, TypeError, KeyError):
        pass
    groups = build_games(events, states, quotes, known_times)
    today = now.date().isoformat()
    games, spreads, totals = [], [], []
    for day, day_games in sorted(groups.items()):
        if day < today:
            continue
        if len(day_games) > 15:
            raise ValueError(f'Unexpected NBA game count for {day}: {len(day_games)}')
        if len(games) + len(day_games) > 500:
            raise ValueError('Too many games in RH result; refusing to publish')
        sp = spread_groups(events, quotes, day, TEAMS)
        to = total_groups(events, quotes, day, TEAMS)
        for game in day_games:
            if game['status'] == 'final':
                continue
            row = dict(game, usDate=day)
            games.append(row)
            sg = sp.get(game['id'], {'id': game['id'], 'lines': []})
            tg = to.get(game['id'], {'id': game['id'], 'lines': []})
            spreads.append(sg)
            totals.append(tg)
    if not games:
        # Do not replace previously useful data with an empty scrape.
        raise RuntimeError('RH returned no upcoming NBA game market records')
    payload = {
        'timezone': 'America/Los_Angeles',
        'source': 'Robinhood public page (unofficial; rendered page only)',
        'capturedAt': now.isoformat(timespec='seconds'),
        'games': games, 'spreads': spreads, 'totals': totals,
    }
    # Update capturedAt after every successful scrape, even if quotes are unchanged.
    # The frontend fails closed after 20 minutes without a successful refresh.
    DEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Upcoming RH NBA markets: {len(games)} games across '
          f'{len(set(g["usDate"] for g in games))} dates')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('Upcoming markets scrape failed; previous snapshot retained:', exc, file=sys.stderr)
        sys.exit(1)

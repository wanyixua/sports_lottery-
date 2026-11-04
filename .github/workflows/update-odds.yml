#!/usr/bin/env python3
"""Safely sync normalized NBA odds from a configured HTTPS JSON source.

Source JSON must use the schema of odds.json. Does not scrape Robinhood.
If source absent or invalid, odds.json is left untouched; error is visible in Actions.
"""
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
from datetime import datetime
from zoneinfo import ZoneInfo

OUT = Path(__file__).resolve().parents[1] / 'odds.json'
SOURCE = os.getenv('ODDS_SOURCE_URL', '').strip()


def validate(data):
    if not isinstance(data, dict):
        raise ValueError('root must be a JSON object')
    date = data.get('date')
    if not isinstance(date, str):
        raise ValueError('date must be YYYY-MM-DD')
    datetime.strptime(date, '%Y-%m-%d')
    games = data.get('games')
    if not isinstance(games, list) or not 1 <= len(games) <= 30:
        raise ValueError('games must contain 1–30 games')
    seen = set()
    for i, game in enumerate(games):
        if not isinstance(game, dict) or not isinstance(game.get('id'), str):
            raise ValueError(f'game[{i}] requires id')
        if game['id'] in seen:
            raise ValueError(f'duplicate game id: {game["id"]}')
        seen.add(game['id'])
        for side in ('away', 'home'):
            team = game.get(side)
            if not isinstance(team, dict):
                raise ValueError(f'game[{i}].{side} must be object')
            for key in ('code', 'nameZh'):
                if not isinstance(team.get(key), str) or not team[key].strip():
                    raise ValueError(f'game[{i}].{side}.{key} required')
            price = team.get('pricePercent')
            if price is not None and (type(price) not in (int, float) or not 0 < price <= 100):
                raise ValueError(f'game[{i}].{side}.pricePercent outside (0,100]')
    return data


def main():
    if not SOURCE:
        print('ODDS_SOURCE_URL not configured: keeping existing snapshot, no RH data fetched.')
        return
    p = urlsplit(SOURCE)
    if p.scheme != 'https' or not p.hostname or p.username or p.password:
        raise ValueError('ODDS_SOURCE_URL must be an HTTPS URL without embedded credentials')
    request = Request(SOURCE, headers={'User-Agent': 'NBA-Odds-Cache/1.0', 'Accept': 'application/json'})
    with urlopen(request, timeout=20) as response:
        raw = response.read(512_001)
    if len(raw) > 512_000:
        raise ValueError('remote JSON too large')
    data = validate(json.loads(raw.decode('utf-8')))
    # Source is responsible for accurate source attribution and price timestamp.
    # Never assert RH origin or fresh quotes without reliable upstream evidence.
    data['isLive'] = False
    data['lastSyncedAt'] = datetime.now(ZoneInfo('America/Los_Angeles')).isoformat(timespec='seconds')
    serialized = json.dumps(data, indent=2, ensure_ascii=False) + '\n'
    if OUT.exists() and OUT.read_text(encoding='utf-8') == serialized:
        print('No changes to odds data')
        return
    temp = OUT.with_suffix('.json.tmp')
    temp.write_text(serialized, encoding='utf-8')
    temp.replace(OUT)
    print(f'Updated {OUT} from configured HTTPS JSON source: {len(data["games"])} games')


if __name__ == '__main__':
    main()

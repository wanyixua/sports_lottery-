#!/usr/bin/env python3
"""Update GitHub Pages odds.json from Robinhood's public NBA page Next.js data.

Experimental, unofficial public-page access. Fail closed on incomplete data.
No credentials required. The frontend continues to read odds.json as before.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
from html.parser import HTMLParser

URL = 'https://robinhood.com/us/en/prediction-markets/nba/games/'
PT = ZoneInfo('America/Los_Angeles')
DEST = Path(__file__).resolve().parents[1] / 'odds.json'
TEAMS = dict(zip(
    'ATL BOS BKN CHA CHI CLE DAL DEN DET GSW HOU IND LAC LAL MEM MIA MIL MIN NOP NYK OKC ORL PHI PHX POR SAC SAS TOR UTA WAS'.split(),
    '老鹰 凯尔特人 篮网 黄蜂 公牛 骑士 独行侠 掘金 活塞 勇士 火箭 步行者 快船 湖人 灰熊 热火 雄鹿 森林狼 鹈鹕 尼克斯 雷霆 魔术 76人 太阳 开拓者 国王 马刺 猛龙 爵士 奇才'.split()
))

class NextData(HTMLParser):
    def __init__(self):
        super().__init__(); self.active = False; self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag == 'script' and dict(attrs).get('id') == '__NEXT_DATA__': self.active = True
    def handle_endtag(self, tag):
        if tag == 'script': self.active = False
    def handle_data(self, data):
        if self.active: self.parts.append(data)


def fetch_page():
    req = Request(URL, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'text/html'})
    with urlopen(req, timeout=35) as resp:
        if resp.status != 200: raise RuntimeError(f'HTTP {resp.status}')
        return resp.read().decode('utf-8')


def parse(html):
    p = NextData(); p.feed(html)
    if not p.parts: raise RuntimeError('Robinhood __NEXT_DATA__ missing')
    root = json.loads(''.join(p.parts))['props']['pageProps']
    return root['events'], root['eventStates'], root['quotes']


def build_games(events, states, quotes):
    dates = {}
    for event in events:
        state = states.get(event.get('id'), {})
        start = state.get('gameStart')
        if not start or not event.get('isSports'): continue
        local = datetime.fromisoformat(start.replace('Z','+00:00')).astimezone(PT)
        contracts = [c for c in event.get('eventContracts', {}).values()
                     if c.get('isTeamContract') and not c.get('isCombo')]
        if len(contracts) != 2: continue
        teams = {}
        for c in contracts:
            code = c.get('displayShortName', '').upper()
            if code not in TEAMS: break
            q = quotes.get(c.get('id'), {})
            price_str = q.get('yes_ask_price')
            if price_str is None: break
            try: price = Decimal(str(price_str))
            except InvalidOperation: break
            if not (Decimal('0') < price <= Decimal('1')): break
            # Report ask quote as percentage (RH display may show different rounded market price).
            teams[code] = {
                'code': code,
                'nameZh': TEAMS[code],
                'record': state.get('contractIdToQuaternaryText', {}).get(c['id'], ''),
                'color': c.get('color', {}).get('hex', {}).get('light', '#64748b'),
                'pricePercent': float(price * 100)
            }
        if len(teams) != 2: continue
        # Away/home order from NBAGAME symbol, e.g. PHIDET. If absent, use stable event name order.
        match = None
        for c in contracts:
            import re
            match = re.search(r'NBAGAME-\d{2}[A-Z]{3}\d{2}([A-Z]{6})-', c.get('symbol', ''))
            if match: break
        if match:
            away, home = match.group(1)[:3], match.group(1)[3:]
        else:
            # Fallback to event contract display order, not alphabetic.
            away, home = [c['displayShortName'].upper() for c in contracts]
        if away not in teams or home not in teams: continue
        day = local.date().isoformat()
        games = dates.setdefault(day, {})
        key = f'{day}-{away}-{home}'
        games[key] = {'id': key, 'startTimePT': local.strftime('%H:%M'), 'away': teams[away], 'home': teams[home]}
    return {day: sorted(rows.values(), key=lambda x:(x['startTimePT'],x['id'])) for day, rows in dates.items()}


def main():
    now = datetime.now(PT)
    groups = build_games(*parse(fetch_page()))
    today = now.date()
    # RH can show future days ahead. Prefer today's games, else nearest upcoming day.
    candidates = sorted(d for d in groups if today.isoformat() <= d <= (today + timedelta(days=7)).isoformat())
    if not candidates: raise RuntimeError('No current or upcoming NBA games in RH HTML')
    target = candidates[0]
    games = groups[target]
    if not (1 <= len(games) <= 15): raise RuntimeError(f'Invalid game count: {len(games)}')
    # Known verification fixture: 2026-11-04 has exactly 14 NBA games.
    if target == '2026-11-04' and len(games) != 14:
        raise RuntimeError(f'Expected 14 Nov 4 games but parsed {len(games)}; cache unchanged')
    # Protect against partial fetches for the same date; never shrink an already published full slate.
    if DEST.exists():
        prev = json.loads(DEST.read_text(encoding='utf-8'))
        if prev.get('date') == target and len(prev.get('games', [])) > len(games):
            raise RuntimeError('Source game count smaller than existing cache; not overwriting')
    payload = {
        'date': target, 'timezone': 'America/Los_Angeles',
        'source': 'Robinhood public webpage (unofficial)',
        'capturedAt': now.isoformat(timespec='seconds'), 'isLive': False,
        'stakeUSD': 10, 'games': games
    }
    # Prevent repetitive commits when prices and game metadata haven't changed.
    prev = None
    if DEST.exists():
        try: prev = json.loads(DEST.read_text(encoding='utf-8'))
        except Exception: pass
    if prev and prev.get('date') == target and prev.get('games') == games and prev.get('source') == payload['source']:
        print(f'No price changes: {target}, {len(games)} games')
        return
    DEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Updated odds.json: {target}, {len(games)} games, {len(games)*2} quotes')

if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print(f'RH data update failed, odds.json preserved: {exc}', file=sys.stderr)
        sys.exit(1)

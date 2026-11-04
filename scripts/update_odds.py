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
from spread_extract import spread_groups
from total_extract import total_groups
from champions_extract import championships

URL = 'https://robinhood.com/us/en/prediction-markets/nba/games/'
PT = ZoneInfo('America/Los_Angeles')
DEST = Path(__file__).resolve().parents[1] / 'odds.json'
SPREAD_DEST = DEST.parent / 'spreads.json'
TOTAL_DEST = DEST.parent / 'totals.json'
CHAMPION_DEST = DEST.parent / 'champions.json'
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


def save_if_changed(path, payload, unchanged_fields):
    # Keep capturedAt stable unless actual contents change (no noisy commits).
    previous = None
    if path.exists():
        try: previous = json.loads(path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError): pass
    if previous and all(previous.get(k) == payload.get(k) for k in unchanged_fields):
        print(f'No changes: {path.name}')
        return False
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Updated: {path.name}')
    return True


def process_html(html, now, *, dest=DEST, spread_dest=SPREAD_DEST, total_dest=TOTAL_DEST, champion_dest=CHAMPION_DEST):
    events, states, quotes = parse(html)
    groups = build_games(events, states, quotes)
    today = now.date()
    candidates = sorted(d for d in groups if today.isoformat() <= d <= (today + timedelta(days=7)).isoformat())
    if not candidates: raise RuntimeError('No current or upcoming NBA games in RH HTML')
    target = candidates[0]
    games = groups[target]
    if not (1 <= len(games) <= 15): raise RuntimeError(f'Invalid game count: {len(games)}')
    if target == '2026-11-04' and len(games) != 14:
        raise RuntimeError(f'Expected 14 Nov 4 games but parsed {len(games)}')
    if dest.exists():
        prev = json.loads(dest.read_text(encoding='utf-8'))
        if prev.get('date') == target and len(prev.get('games', [])) > len(games):
            raise RuntimeError('Source game count smaller than existing cache')

    spreads = spread_groups(events, quotes, target, TEAMS)
    # Important: allow some unavailable ask prices, but require complete market discovery.
    # Do not overwrite good spread cache with partial market lists.
    if set(spreads) != {g['id'] for g in games}:
        raise RuntimeError(f'Incomplete spread discovery: {len(spreads)}/{len(games)} games; caches preserved')
    if spread_dest.exists():
        try: previous_spreads = json.loads(spread_dest.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError): previous_spreads = None
        if previous_spreads and previous_spreads.get('date') == target:
            old_by_game = {g['id']: len(g.get('lines', [])) for g in previous_spreads.get('games', [])}
            if any(len(spreads[k]['lines']) < old_by_game.get(k, 0) for k in spreads):
                raise RuntimeError('Spread contract counts shrank; caches preserved')
    totals = total_groups(events, quotes, target, TEAMS)
    if set(totals) != {g['id'] for g in games}:
        raise RuntimeError(f'Incomplete total discovery: {len(totals)}/{len(games)} games; caches preserved')
    if total_dest.exists():
        try: previous_totals = json.loads(total_dest.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError): previous_totals = None
        if previous_totals and previous_totals.get('date') == target:
            old_sizes = {g['id']: len(g.get('lines', [])) for g in previous_totals.get('games', [])}
            if any(len(totals[key]['lines']) < old_sizes.get(key, 0) for key in totals):
                raise RuntimeError('Totals contract counts shrank; caches preserved')
    nowstr = now.isoformat(timespec='seconds')
    odds_payload = {
        'date': target, 'timezone': 'America/Los_Angeles',
        'source': 'Robinhood public webpage (unofficial)', 'capturedAt': nowstr,
        'isLive': False, 'stakeUSD': 10, 'games': games,
    }
    spread_payload = {
        'date': target, 'timezone': 'America/Los_Angeles',
        'source': 'Robinhood public webpage (unofficial)', 'capturedAt': nowstr,
        'games': [spreads[g['id']] for g in games],
    }
    total_payload = {
        'date': target, 'timezone': 'America/Los_Angeles',
        'source': 'Robinhood public webpage (unofficial)', 'capturedAt': nowstr,
        'games': [totals[g['id']] for g in games],
    }
    total_contracts = sum(len(g['lines']) for g in total_payload['games'])
    total_quotes = sum(line[side] is not None for g in total_payload['games'] for line in g['lines']
                       for side in ('overPricePercent','underPricePercent'))
    if not total_quotes:
        raise RuntimeError('No valid total ask quotes; caches preserved')
    print(f'{total_contracts} total lines; {total_quotes} valid Over/Under ask prices')
    quoted = sum(line['pricePercent'] is not None for g in spread_payload['games'] for line in g['lines'])
    count = sum(len(g['lines']) for g in spread_payload['games'])
    if quoted == 0:
        raise RuntimeError('No valid spread ask quotes; caches preserved')
    print(f'{target}: {len(games)} games, {count} spread contracts, {quoted} valid ask prices')
    champion_markets = championships(events, quotes, TEAMS)
    quoted_champions = sum(t['pricePercent'] is not None for m in champion_markets.values() for t in m['teams'])
    if not quoted_champions: raise RuntimeError('No champion YES ask quotes; caches preserved')
    save_if_changed(dest, odds_payload, ('date', 'source', 'games'))
    save_if_changed(spread_dest, spread_payload, ('date', 'source', 'games'))
    save_if_changed(total_dest, total_payload, ('date', 'source', 'games'))
    champion_payload = {
        'season': '2026-27', 'source': 'Robinhood public webpage (unofficial)',
        'capturedAt': nowstr, 'markets': champion_markets,
    }
    quoted_champions = sum(t['pricePercent'] is not None for m in champion_markets.values() for t in m['teams'])
    if not quoted_champions:
        raise RuntimeError('No champion YES ask quotes; cache preserved')
    print(f'Champions: {sum(len(m["teams"]) for m in champion_markets.values())} contracts, {quoted_champions} valid ask prices')
    save_if_changed(champion_dest, champion_payload, ('season', 'source', 'markets'))
    return odds_payload, spread_payload, total_payload, champion_payload


def main():
    now = datetime.now(PT)
    process_html(fetch_page(), now)

if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print(f'RH data update failed, caches preserved where possible: {exc}', file=sys.stderr)
        sys.exit(1)

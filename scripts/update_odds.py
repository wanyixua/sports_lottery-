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
from urllib.parse import urlencode
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


def load_existing_game_times(dest):
    """Keep original start times for games whose live state has no gameStart."""
    try:
        saved = json.loads(dest.read_text(encoding='utf-8'))
        return {g['id']: g['startTimePT'] for g in saved.get('games', [])
                if g.get('id') and g.get('startTimePT')}
    except (OSError, ValueError, TypeError, KeyError):
        return {}


def build_games(events, states, quotes, existing_times=None):
    """Discover NBA game dates from contract symbols, including live games.

    RH removes eventStates[event_id].gameStart after tipoff, while retaining
    event, team contracts, eventStatus, and quotes. The game symbol still
    provides its date and away/home pairing. Reuse cached exact start time
    if gameStart is unavailable; never drop the game merely for that reason.
    """
    import re
    existing_times = existing_times or {}
    dates = {}
    for event in events:
        if not event.get('isSports'):
            continue
        state = states.get(event.get('id'), {})
        contracts = [c for c in (event.get('eventContracts') or {}).values()
                     if c.get('isTeamContract') and not c.get('isCombo')]
        if len(contracts) != 2:
            continue
        matches = [re.fullmatch(r'NBAGAME-(\d{2}[A-Z]{3}\d{2})([A-Z]{6})-([A-Z]{3})',
                               str(c.get('symbol', ''))) for c in contracts]
        if any(m is None for m in matches):
            continue
        stamp, pair, _ = matches[0].groups()
        if any(m.group(1) != stamp or m.group(2) != pair for m in matches):
            continue
        try:
            symbol_day = datetime.strptime(stamp, '%y%b%d').date().isoformat()
        except ValueError:
            continue
        away, home = pair[:3], pair[3:]
        if away not in TEAMS or home not in TEAMS:
            continue
        teams = {}
        for c in contracts:
            code = str(c.get('displayShortName', '')).upper()
            if code not in (away, home):
                break
            q = quotes.get(c.get('id'), {}) or {}
            price_str = q.get('yes_ask_price')
            price_percent = None
            if price_str is not None:
                try:
                    price = Decimal(str(price_str))
                    if Decimal('0') < price <= Decimal('1'):
                        price_percent = float(price * 100)
                except (InvalidOperation, ValueError, TypeError):
                    pass
            teams[code] = {
                'code': code,
                'nameZh': TEAMS[code],
                'record': (state.get('contractIdToQuaternaryText') or {}).get(c['id'], ''),
                'color': c.get('color', {}).get('hex', {}).get('light', '#64748b'),
                'pricePercent': price_percent,
            }
        if set(teams) != {away, home}:
            continue
        key = f'{symbol_day}-{away}-{home}'
        start = state.get('gameStart')
        if start:
            try:
                local = datetime.fromisoformat(start.replace('Z', '+00:00')).astimezone(PT)
                if local.date().isoformat() != symbol_day:
                    raise ValueError(f'Game start / symbol date mismatch: {key}')
                start_time = local.strftime('%H:%M')
            except (ValueError, AttributeError):
                start_time = existing_times.get(key)
        else:
            start_time = existing_times.get(key)
        if not start_time:
            # The initial RH page lacks kickoff time for some live events.
            # Preserve those games and explicitly mark kickoff as unknown.
            start_time = '00:00'
            print(f'Warning: no gameStart/cache for {key}; kickoff unknown (live game retained)')
        games = dates.setdefault(symbol_day, {})
        rh_status = str(state.get('eventStatus') or '')
        progress = str(state.get('eventProgress') or '').strip()
        # Only publish a clock when RH explicitly marks this game as in progress.
        live = rh_status == 'EVENT_STATUS_IN_PROGRESS'
        if rh_status in ('EVENT_STATUS_COMPLETED', 'EVENT_STATUS_SETTLED', 'EVENT_STATUS_FINAL'):
            for side in teams.values():
                side['pricePercent'] = None
        games[key] = {
            'id': key, 'startTimePT': start_time,
            'status': 'live' if live else ('final' if rh_status in ('EVENT_STATUS_COMPLETED', 'EVENT_STATUS_SETTLED', 'EVENT_STATUS_FINAL') else 'upcoming'),
            'progress': progress if live else '',
            'away': teams[away], 'home': teams[home],
        }
    return {day: sorted(rows.values(), key=lambda x: (x['startTimePT'], x['id']))
            for day, rows in dates.items()}


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


def read_previous(path, date):
    """Return only a valid previous cache for the exact same slate date."""
    try:
        obj = json.loads(path.read_text(encoding='utf-8'))
        if obj.get('date') == date and isinstance(obj.get('games'), list):
            return obj
    except (OSError, ValueError, AttributeError):
        pass
    return None


# ESPN publishes the final outcome independently of RH's tradable markets.
# This public scoreboard endpoint is unofficial and may be unavailable.
ESPN_SCOREBOARD = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard'
ESPN_CODES = {
    'NY': 'NYK', 'GS': 'GSW', 'SA': 'SAS', 'NO': 'NOP',
    'WSH': 'WAS', 'UTAH': 'UTA', 'PHO': 'PHX',
}


def fetch_espn_scoreboard(day):
    url = ESPN_SCOREBOARD + '?' + urlencode({'dates': day.replace('-', ''), 'limit': '100'})
    req = Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
    with urlopen(req, timeout=15) as resp:
        if resp.status != 200:
            raise RuntimeError(f'ESPN scoreboard HTTP {resp.status}')
        return json.load(resp)


def completed_espn_games(scoreboard, day):
    """Only unambiguous *final* events on the PT schedule day."""
    finals = set()
    for event in scoreboard.get('events', []):
        try:
            status = (event.get('status') or {}).get('type') or {}
            # A cancelled/abandoned event is not a normal final even when post-game.
            if not (status.get('completed') is True and
                    (status.get('name') == 'STATUS_FINAL' or str(status.get('id')) == '3')):
                continue
            comps = event.get('competitions') or []
            if not comps:
                continue
            comp = comps[0]
            competitors = comp.get('competitors') or []
            if len(competitors) != 2:
                continue
            teams = {}
            for c in competitors:
                code = str((c.get('team') or {}).get('abbreviation') or '').upper()
                code = ESPN_CODES.get(code, code)
                teams[c.get('homeAway')] = code
            home, away = teams.get('home'), teams.get('away')
            if home not in TEAMS or away not in TEAMS:
                continue
            start = datetime.fromisoformat(str(event['date']).replace('Z', '+00:00'))
            if start.astimezone(PT).date().isoformat() != day:
                continue
            finals.add(f'{day}-{away}-{home}')
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
    return finals


def retain_missing_games(current, previous, *, fetch_scores=fetch_espn_scoreboard):
    """Keep removed games visible, but NEVER present old quotes as tradable.

    RH removes markets after tipoff/settlement. Disappearance alone is not
    proof of final; verify the exact matchup/date with ESPN instead.
    """
    by_id = {g['id']: g for g in current}
    missing = [g for g in (previous or {}).get('games', []) if g.get('id') not in by_id]
    verified_finals = set()
    if missing:
        day = previous.get('date')
        if day:
            try:
                verified_finals = completed_espn_games(fetch_scores(day), day)
                print(f'ESPN confirmed {len(verified_finals)} final matchups on {day}')
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
                print(f'Warning: ESPN results unavailable ({exc}); missing RH games stay unavailable')
    if previous:
        for old in previous.get('games', []):
            key = old.get('id')
            if not key or key in by_id:
                continue
            preserved = json.loads(json.dumps(old))
            # A previously confirmed final remains final even if ESPN is down.
            preserved['status'] = 'final' if (key in verified_finals or old.get('status') == 'final') else 'unavailable'
            preserved['progress'] = ''
            for side in ('away', 'home'):
                if isinstance(preserved.get(side), dict):
                    preserved[side]['pricePercent'] = None
            by_id[key] = preserved
            print(f'RH removed game {key}; status={preserved["status"]}; quotes unavailable')
    return sorted(by_id.values(), key=lambda g: (g.get('startTimePT') or '99:99', g['id']))


def retain_missing_market_lines(current, previous, *, kind):
    """Preserve contract labels for removed markets with all quotes cleared."""
    old_by_id = {g['id']: g for g in (previous or {}).get('games', [])}
    by_id = {key: json.loads(json.dumps(g)) for key, g in current.items()}
    price_fields = ('pricePercent',) if kind == 'spread' else ('overPricePercent', 'underPricePercent')
    for game_id, old in old_by_id.items():
        existing = by_id.setdefault(game_id, {'id': game_id, 'lines': []})
        fresh_lines = existing['lines']
        fresh_ids = {line.get('id') for line in fresh_lines}
        for line in old.get('lines', []):
            if line.get('id') in fresh_ids:
                continue
            stale = dict(line)
            for field in price_fields:
                stale[field] = None
            fresh_lines.append(stale)
        if kind == 'spread':
            fresh_lines.sort(key=lambda line: (line.get('team', ''), line.get('margin', 0)))
        else:
            fresh_lines.sort(key=lambda line: line.get('points', 0))
    return by_id


def process_html(html, now, *, dest=DEST, spread_dest=SPREAD_DEST, total_dest=TOTAL_DEST, champion_dest=CHAMPION_DEST):
    events, states, quotes = parse(html)
    groups = build_games(events, states, quotes, load_existing_game_times(dest))
    today = now.date()
    candidates = sorted(d for d in groups if today.isoformat() <= d <= (today + timedelta(days=7)).isoformat())
    # Keep today's cached slate when RH removed *all* its markets.
    # Otherwise the page could jump to tomorrow without ever marking the
    # remaining matchups final. Tomorrow becomes active on the next PT day.
    cached_today = read_previous(dest, today.isoformat())
    if today.isoformat() in groups:
        target = today.isoformat()
    elif cached_today and (candidates or not groups):
        target = today.isoformat()
        print('RH has no markets for today; checking cached slate against ESPN finals')
    elif candidates:
        target = candidates[0]
    else:
        raise RuntimeError('No current or upcoming NBA games in RH HTML')
    games = groups.get(target, [])
    if len(games) > 15 or (not games and not cached_today):
        raise RuntimeError(f'Invalid game count: {len(games)}')
    valid_winner_quotes = sum(team['pricePercent'] is not None for game in games for team in (game['away'], game['home']))
    if valid_winner_quotes == 0:
        print('Warning: no active winner ask quotes; preserving available game identities')
    print(f'Parsed {len(games)} scheduled games, {valid_winner_quotes}/{2 * len(games)} winner ask quotes')
    previous_odds = read_previous(dest, target)
    merged_games = retain_missing_games(games, previous_odds)

    # RH legitimately removes some spread/total contracts as games start or
    # finish. Removed contracts retain their labels but NOT obsolete prices.
    spreads = spread_groups(events, quotes, target, TEAMS)
    totals = total_groups(events, quotes, target, TEAMS)
    spreads = retain_missing_market_lines(spreads, read_previous(spread_dest, target), kind='spread')
    totals = retain_missing_market_lines(totals, read_previous(total_dest, target), kind='total')
    for game in merged_games:
        spread = spreads.setdefault(game['id'], {'id': game['id'], 'lines': []})
        total = totals.setdefault(game['id'], {'id': game['id'], 'lines': []})
        if game.get('status') in ('final', 'unavailable'):
            for line in spread['lines']:
                line['pricePercent'] = None
            for line in total['lines']:
                line['overPricePercent'] = None
                line['underPricePercent'] = None

    # If an entire game vanished, old lines were retained with no old asks.
    # Do not overwrite a healthy full slate with a zero-game parse.
    games = merged_games
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
        print('Warning: no live totals ask quotes; publishing unavailable prices')
    print(f'{total_contracts} total lines; {total_quotes} valid Over/Under ask prices')
    quoted = sum(line['pricePercent'] is not None for g in spread_payload['games'] for line in g['lines'])
    count = sum(len(g['lines']) for g in spread_payload['games'])
    if quoted == 0:
        print('Warning: no live spread ask quotes; publishing unavailable prices')
    print(f'{target}: {len(games)} games, {count // 2} spread contracts, {count} spread directions (YES/NO), {quoted} valid ask prices')
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

"""Read NBA spread contracts from Robinhood public Next.js page data.

A spread YES contract represents 'TEAM wins by more than X.5 points'.
No estimates are invented for missing asks. Not a supported Robinhood API.
"""
import re
from decimal import Decimal, InvalidOperation

SYMBOL = re.compile(r'^NBASPREAD-\d{2}[A-Z]{3}\d{2}([A-Z]{6})-([A-Z]{3})(\d+)$')


def spread_groups(events, quotes, date, teams):
    output = {}
    for event in events:
        if 'Spreads' not in event.get('name', ''):
            continue
        contracts = list((event.get('eventContracts') or {}).values())
        rows = []
        game_id = None
        for contract in contracts:
            m = SYMBOL.fullmatch(contract.get('symbol', ''))
            if not m or not contract.get('isTeamContract') or contract.get('isCombo'):
                continue
            awayhome, team, _ = m.groups()
            away, home = awayhome[:3], awayhome[3:]
            if team not in (away, home) or away not in teams or home not in teams:
                continue
            # Symbol date matches requested date to avoid cross-day joins.
            prefix = contract['symbol'].split('-')[1]
            from datetime import datetime
            if datetime.strptime(prefix[:7], '%y%b%d').date().isoformat() != date:
                continue
            expected_id = f'{date}-{away}-{home}'
            if game_id is not None and game_id != expected_id:
                raise ValueError(f'Inconsistent event mappings: {event.get("id")}')
            game_id = expected_id
            raw_margin = contract.get('floorStrikeValue', '')
            try:
                margin = Decimal(str(raw_margin))
            except InvalidOperation:
                continue
            if margin <= 0 or margin > 100:
                continue
            price = None
            raw_price = (quotes.get(contract.get('id')) or {}).get('yes_ask_price')
            if raw_price is not None:
                try:
                    n = Decimal(str(raw_price))
                    if Decimal('0') < n <= Decimal('1'):
                        price = float(n * 100)
                except InvalidOperation:
                    pass
            rows.append({
                'id': contract['id'],
                'team': team,
                'teamNameZh': teams[team],
                'margin': float(margin),
                'pricePercent': price,
            })
        if not rows or not game_id:
            continue
        if game_id in output:
            raise ValueError(f'Duplicate spread market: {game_id}')
        rows.sort(key=lambda x: (x['team'], x['margin']))
        if len({x['id'] for x in rows}) != len(rows):
            raise ValueError(f'Duplicate contract ids: {game_id}')
        output[game_id] = {'id': game_id, 'lines': rows}
    return output

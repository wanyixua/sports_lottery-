#!/usr/bin/env python3
"""Parse 2027 NBA champion, Western champion and Eastern champion from RH public Next.js events.

Uses YES ask, never displayed probability or a synthetic quote.
"""
from decimal import Decimal, InvalidOperation

MARKETS = {
    'finals': ('2027 Pro Men\'s Basketball Champion', 'NBA-27-'),
    'west': ('2027 Pro Men\'s Basketball Western Conference Champion', 'NBAWEST-27-'),
    'east': ('2027 Pro Men\'s Basketball Eastern Conference Champion', 'NBAEAST-27-'),
}
ROOT_URL = 'https://robinhood.com/us/en/prediction-markets/mens-pro-basketball/events/'


def championships(events, quotes, team_names):
    output = {}
    for key, (event_name, symbol_prefix) in MARKETS.items():
        matches = [e for e in events if e.get('name') == event_name]
        if len(matches) != 1:
            raise ValueError(f'Champion event {key}: expected 1, found {len(matches)}')
        event = matches[0]
        items = []
        for contract in event.get('eventContracts', {}).values():
            code = contract.get('displayShortName', '').upper()
            if code not in team_names or not contract.get('symbol', '').startswith(symbol_prefix):
                continue
            q = quotes.get(contract.get('id'), {})
            price = None
            if q.get('yes_ask_price') is not None:
                try:
                    p = Decimal(str(q['yes_ask_price']))
                    if Decimal('0') < p <= Decimal('1'):
                        price = float(p * 100)
                except (InvalidOperation, ValueError):
                    pass
            items.append({
                'id': contract['id'], 'code': code, 'nameZh': team_names[code],
                'pricePercent': price,
                'color': contract.get('color', {}).get('hex', {}).get('light', '#64748b'),
            })
        expected = 30 if key == 'finals' else 15
        if len(items) != expected or len({c['code'] for c in items}) != expected:
            raise ValueError(f'Champion {key}: expected {expected} teams, found {len(items)}')
        items.sort(key=lambda c: (c['pricePercent'] is None, -(c['pricePercent'] or 0), c['code']))
        slugs = event.get('urlSlugs') or []
        output[key] = {
            'title': {'finals': 'NBA总冠军', 'west': '西部冠军', 'east': '东部冠军'}[key],
            'url': ROOT_URL + slugs[0] + '/' if slugs else '',
            'eventId': event['id'], 'teams': items,
        }
    return output

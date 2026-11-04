"""Extract NBA game-total contracts: YES ask = Over, NO ask = Under."""
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

SYMBOL = re.compile(r'^NBATOTAL-(\d{2}[A-Z]{3}\d{2})([A-Z]{6})-(\d+)$')

def _ask(quote, side):
    raw = (quote or {}).get(f'{side}_ask_price')
    try:
        x = Decimal(str(raw))
        return float(x*100) if Decimal('0') < x <= Decimal('1') else None
    except (InvalidOperation, TypeError, ValueError):
        return None

def total_groups(events, quotes, date, teams):
    output = {}
    for event in events:
        if event.get('eventType') != 'EVENT_TYPE_TOTALS' or not event.get('name','').endswith(' Totals'):
            continue
        rows=[]; game_id=None
        for contract in (event.get('eventContracts') or {}).values():
            m=SYMBOL.fullmatch(contract.get('symbol',''))
            if not m or contract.get('isCombo') or not contract.get('isTeamContract'):
                continue
            stamp, pair, _=m.groups()
            if datetime.strptime(stamp,'%y%b%d').date().isoformat()!=date:
                continue
            away,home=pair[:3],pair[3:]
            if away not in teams or home not in teams:
                continue
            key=f'{date}-{away}-{home}'
            if game_id and key!=game_id:
                raise ValueError('Mixed NBA total games in one event')
            game_id=key
            try: points=Decimal(str(contract.get('floorStrikeValue')))
            except (InvalidOperation, TypeError): continue
            if not (Decimal('100') < points < Decimal('400')): continue
            quote=quotes.get(contract.get('id')) or {}
            rows.append({'id':contract['id'],'points':float(points),
                         'overPricePercent':_ask(quote,'yes'),
                         'underPricePercent':_ask(quote,'no')})
        if not game_id or not rows: continue
        if game_id in output: raise ValueError('Duplicate NBA total event '+game_id)
        rows.sort(key=lambda x:x['points'])
        if len({x['id'] for x in rows}) != len(rows): raise ValueError('Duplicate total contracts')
        output[game_id]={'id':game_id,'lines':rows}
    return output

#!/usr/bin/env python3
"""Fail-safe audit for Xiaowang's NBA ledger. No writes to transactions.json."""
import argparse
import json
import math
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ALLOWED = {'pending', 'review', 'win', 'loss', 'win_needs_amount'}
FINAL = {'win', 'loss', 'win_needs_amount'}
DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def read_ledger(raw, name):
    try:
        d = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError(f'{name}: invalid JSON: {exc}') from exc
    if not isinstance(d, dict) or not isinstance(d.get('records'), list):
        raise ValueError(f'{name}: missing records array')
    return d['records']


def prior_revision(path):
    # Choose the immediately previous *change to the ledger*, not HEAD^.
    p = subprocess.run(['git', 'log', '--format=%H', '-n', '2', '--', path], capture_output=True, text=True, check=True)
    revs = p.stdout.splitlines()
    if len(revs) < 2:
        return None
    p = subprocess.run(['git', 'show', f'{revs[1]}:{path}'], capture_output=True, text=True)
    if p.returncode != 0:
        return None
    return read_ledger(p.stdout, f'previous revision {revs[1][:9]}')


def check(rows, before=None):
    errors, warnings = [], []
    ids = [r.get('id') if isinstance(r, dict) else None for r in rows]
    counts = Counter(str(i) for i in ids)
    for k, n in counts.items():
        if n > 1: errors.append(f'Duplicate ID {k}: {n} entries')
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            errors.append(f'Row #{i+1} is not an object'); continue
        ident = r.get('id', f'row#{i+1}')
        if not isinstance(r.get('id'), int) or isinstance(r['id'], bool) or r['id'] <= 0:
            errors.append(f'{ident}: invalid ID')
        if not isinstance(r.get('owner'), str) or not r['owner'].strip():
            errors.append(f'{ident}: missing owner')
        if not isinstance(r.get('bet'), str) or not r['bet'].strip():
            errors.append(f'{ident}: missing bet')
        if not isinstance(r.get('usDate'), str) or not DATE.fullmatch(r['usDate']):
            errors.append(f'{ident}: invalid usDate')
        status = r.get('status')
        if status not in ALLOWED: errors.append(f'{ident}: unrecognized status {status!r}')
        stake = r.get('stake')
        if isinstance(stake, bool) or not isinstance(stake, (int,float)) or not math.isfinite(stake) or stake <= 0:
            errors.append(f'{ident}: stake must be a positive number')
        payout = r.get('payout')
        if payout is not None and (isinstance(payout, bool) or not isinstance(payout,(int,float)) or not math.isfinite(payout) or payout < 0):
            errors.append(f'{ident}: invalid payout')
        if status == 'pending' and payout is not None:
            errors.append(f'{ident}: pending order has a payout')
        if status == 'loss' and payout is None:
            errors.append(f'{ident}: loss missing payout')
        if status == 'win' and payout is None:
            errors.append(f'{ident}: win missing payout (use win_needs_amount)')
        if status == 'win_needs_amount' and payout is not None:
            warnings.append(f'{ident}: win_needs_amount already has payout')
        if status == 'win_needs_amount' and r.get('profitIfWin') is None:
            warnings.append(f'{ident}: winning order still needs profit amount')
        if status == 'pending' and r.get('profitIfWin') is None:
            warnings.append(f'{ident}: pending order has no win-profit amount')
    if before is not None:
        prev = {str(r['id']):r for r in before if isinstance(r,dict) and 'id' in r}
        curr = {str(r['id']):r for r in rows if isinstance(r,dict) and 'id' in r}
        for ident in sorted(prev.keys() - curr.keys(), key=lambda s:int(s) if s.isdigit() else s):
            errors.append(f'REGRESSION: transaction #{ident} removed')
        for ident in sorted(prev.keys() & curr.keys()):
            old, new = prev[ident], curr[ident]
            if old.get('status') in FINAL and new.get('status') not in FINAL:
                errors.append(f'REGRESSION: #{ident} {old.get("status")} -> {new.get("status")}')
            if old.get('status') in {'win','loss'} and new.get('status') in {'win','loss'}:
                if old.get('status') != new.get('status'):
                    warnings.append(f'#{ident} outcome changed {old.get("status")} -> {new.get("status")}; verify manual correction')
            for field in ('stake','owner','usDate','bet'):
                if old.get(field) != new.get(field):
                    warnings.append(f'#{ident}: {field} changed; confirm intentional edit')
    return errors, warnings


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--file', default='transactions.json')
    parser.add_argument('--previous-git', action='store_true', help='compare against preceding committed ledger version')
    parser.add_argument('--before-file', help='compare against snapshot taken before a job changed the ledger')
    args=parser.parse_args()
    try:
        rows=read_ledger(Path(args.file).read_text(encoding='utf-8'), args.file)
        before = read_ledger(Path(args.before_file).read_text(encoding='utf-8'), args.before_file) if args.before_file else prior_revision(args.file) if args.previous_git else None
        errors,warnings=check(rows,before)
    except (OSError,ValueError,subprocess.CalledProcessError) as exc:
        print(f'::error::{exc}');return 1
    stats=Counter(r.get('status') for r in rows if isinstance(r,dict))
    print(f'Ledger: {len(rows)} orders, {stats.get("win",0)} wins, {stats.get("loss",0)} losses, {stats.get("pending",0)} pending, {stats.get("review",0)} review, {stats.get("win_needs_amount",0)} amount pending')
    for w in warnings: print('::warning::'+w)
    for e in errors: print('::error::'+e)
    summary=(f'### 交易数据健康检查\n\n记录：**{len(rows)}** | 已赢：{stats.get("win",0)} | 已输：{stats.get("loss",0)} | 待结算：{stats.get("pending",0)}\n\n'
             + ('✅ 无阻断错误\n' if not errors else '❌ **阻止发布／结算**\n\n'+ '\n'.join(f'- {e}' for e in errors)+'\n')
             + (f'\n⚠️ 其他提示：{len(warnings)} 项，见作业日志。\n' if warnings else ''))
    import os
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as f: f.write(summary+'\n')
    return 1 if errors else 0

if __name__=='__main__': sys.exit(main())

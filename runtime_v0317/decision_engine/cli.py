"""JSON-in / JSON-out command line, with no account credentials or network access."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from .engine import recommend, apply_user_pick
from .validation import validate, EngineError

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['validate','recommend','pick','recommend-scaled','import-csv','export-csv','recommend-rules','pick-rules','check-lineup-move','recommend-efficient','pick-efficient'])
    parser.add_argument('input',type=Path)
    parser.add_argument('--out',type=Path)
    parser.add_argument('--sweeps',type=int,default=2,help='Bounded lineup sweeps for scaled/rules/efficient research profiles.')
    parser.add_argument('--bundle-dir',type=Path,help='CSV output directory for export-csv.')
    parser.add_argument('--context',type=Path,help='Explicit lock context for check-lineup-move.')
    parser.add_argument('--player',help='Required for pick; must be available and feasible.')
    args=parser.parse_args()
    if not 0 <= args.sweeps <= 10:parser.error('--sweeps must be between 0 and 10')
    try:
        if args.command=='import-csv':
            from research.manual_csv import import_bundle
            data=import_bundle(args.input)
        else:data=json.loads(args.input.read_text(encoding='utf-8'))
        if args.command=='import-csv':result=data
        elif args.command=='export-csv':
            from research.manual_csv import export_bundle
            if not args.bundle_dir:parser.error('export-csv requires --bundle-dir')
            result=export_bundle(data,args.bundle_dir)
        elif args.command=='recommend-scaled':
            from research.scalable import recommend_scaled
            result=recommend_scaled(data,args.sweeps)
        elif args.command=='recommend-efficient':
            from research.efficient_runner import recommend_efficient
            result=recommend_efficient(data,args.sweeps)
        elif args.command=='pick-efficient':
            from research.efficient_runner import apply_user_pick_efficient
            if not args.player:parser.error('pick-efficient requires --player')
            result=apply_user_pick_efficient(data,args.player)
        elif args.command=='recommend-rules':
            from research.rule_aware import recommend_rules
            result=recommend_rules(data,args.sweeps)
        elif args.command=='pick-rules':
            from research.rule_aware import apply_user_pick_rules
            if not args.player:parser.error('pick-rules requires --player')
            result=apply_user_pick_rules(data,args.player)
        elif args.command=='check-lineup-move':
            from research.lineup_locks import check_lineup_transition
            if not args.context:parser.error('check-lineup-move requires --context')
            result=check_lineup_transition(data,json.loads(args.context.read_text(encoding='utf-8')))
        elif args.command=='validate':validate(data);result={'status':'SCHEMA_AND_SEMANTICS_VALID','note':'Recommendation also validates current-roster construction and terminal feasibility.'}
        elif args.command=='pick':
            if not args.player:parser.error('pick requires --player')
            result=apply_user_pick(data,args.player)
        else:result=recommend(data)
    except (OSError,json.JSONDecodeError) as exc:
        result={'run_status':'INPUT_ERROR','messages':[{'code':'INPUT_FILE_ERROR','message':str(exc)}]}
    except EngineError as exc:
        result={'run_status':exc.status,'messages':[exc.as_dict()]}
    text=json.dumps(result,indent=2,allow_nan=False)+'\n'
    if args.out:
        args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(text,encoding='utf-8')
    else:sys.stdout.write(text)
    return 2 if result.get('run_status') in ('INPUT_ERROR','UNSUPPORTED_CAPABILITY','RESOURCE_LIMIT') else 0

if __name__=='__main__':raise SystemExit(main())

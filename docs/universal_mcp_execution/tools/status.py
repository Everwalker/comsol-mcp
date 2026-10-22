#!/usr/bin/env python3
"""Summarize declared continuation state. This does not certify implementation or tests."""
import argparse,json
from pathlib import Path
from collections import Counter
from bootstrap import ROOT,valid_path,safe_ancestors
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',type=Path,required=True);a=p.parse_args()
    pin=json.loads((ROOT/'PIN.json').read_text(encoding='utf-8'));state=a.repo.joinpath(*valid_path(pin['handoff_directory']))/'state';safe_ancestors(state)
    resume=json.loads((state/'RESUME.json').read_text(encoding='utf-8'));dod=json.loads((state/'DOD_STATUS.json').read_text(encoding='utf-8'));review=json.loads((state/'REVIEW.json').read_text(encoding='utf-8'))
    print(json.dumps({'scope':'DECLARED_STATE_ONLY_NOT_NATIVE_CERTIFICATION','phase':resume['phase'],'next_action':resume['next_action'],'blockers':resume['blockers'],'group_statuses':dict(Counter(x['status'] for x in dod['rows'])),'review_status':review['status']},ensure_ascii=False,indent=2))

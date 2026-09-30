"""Source-readiness checks. Claimed dates are not independently verified archives."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime
from pathlib import Path

REQUIRED_HISTORICAL={'LEAGUE_RULES','PLAYER_IDENTITIES','ELIGIBILITY','PROJECTIONS','CALENDAR','DRAFT_OWNERSHIP','ACTUAL_OUTCOMES'}
PREDRAFT=REQUIRED_HISTORICAL-{'ACTUAL_OUTCOMES'}

def stamp(text):
    value=datetime.fromisoformat(text.replace('Z','+00:00'))
    if value.tzinfo is None:raise ValueError('Timezone-aware timestamp required.')
    return value

def audit_sources(manifest,root=None):
    """Conservative evidence gate; does not infer source rights or vintage."""
    cut=stamp(manifest['decision_cutoff']); records=[]
    for entry in manifest['sources']:
        reasons=[];kind=entry['kind'];published=entry.get('published_at');retrieved=entry.get('retrieved_at')
        if not published:reasons.append('PUBLICATION_VINTAGE_UNVERIFIED')
        elif kind in PREDRAFT and stamp(published)>cut:reasons.append('POST_DECISION_INPUT')
        if entry.get('vintage_evidence')!='VERIFIED_ARCHIVE':reasons.append('NO_VERIFIED_AS_OF_ARCHIVE')
        if kind in PREDRAFT and retrieved and stamp(retrieved)>cut and entry.get('vintage_evidence')!='VERIFIED_ARCHIVE':
            reasons.append('LATE_RETRIEVAL_WITHOUT_AS_OF_PROOF')
        if entry.get('scope_matches') is not True:reasons.append('SCOPE_NOT_VERIFIED')
        path=entry.get('local_path');expected=entry.get('sha256')
        if path and root:
            base=Path(root).resolve();p=(base/path).resolve()
            if not p.is_relative_to(base):reasons.append('UNSAFE_SOURCE_PATH')
            elif not p.is_file():reasons.append('MISSING_LOCAL_BYTES')
            elif not expected or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:reasons.append('SOURCE_HASH_MISMATCH')
        else:reasons.append('NO_LOCAL_HASH_VERIFIED_BYTES')
        if entry.get('research_use_status') not in ('AUTHORIZED','USER_PROVIDED_PRIVATE_RESEARCH'):
            reasons.append('RESEARCH_PERMISSION_UNVERIFIED')
        commercial=entry.get('commercial_use_status')=='LICENSED'
        records.append({'source_id':entry['source_id'],'kind':kind,'historical_ready':not reasons,'blocking_reasons':sorted(set(reasons)),'commercial_ready':not reasons and commercial})
    ready={r['kind'] for r in records if r['historical_ready']};comm={r['kind'] for r in records if r['commercial_ready']}
    missing=sorted(REQUIRED_HISTORICAL-ready)
    return {'status':'READY_FOR_HISTORICAL_INPUTS' if not missing else 'NOT_READY_FOR_HISTORICAL_EVALUATION',
            'missing_or_blocked_kinds':missing,'commercial_ready':REQUIRED_HISTORICAL<=comm,
            'decision_cutoff':manifest['decision_cutoff'],'sources':records,
            'note':'This checks declared provenance and local hashes, not truthfulness of a license or supplier, predictive accuracy, or completeness beyond the manifest.'}

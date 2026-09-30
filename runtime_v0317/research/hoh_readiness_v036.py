"""Keep recovered archive facts distinct from a complete current decision input."""
from pathlib import Path
import hashlib,json,csv
from decision_engine.validation import EngineError


def audit_hoh_archive(archive_directory):
    root=Path(archive_directory)
    required=['verified_archive_settings.json','source_manifest.json','source_players.csv']
    if any(not (root/p).is_file() for p in required):raise EngineError('HOH_ARCHIVE_MISSING','Source archive files are incomplete.')
    settings=json.loads((root/'verified_archive_settings.json').read_text());manifest=json.loads((root/'source_manifest.json').read_text())
    checks=[]
    for entry in manifest['sources']:
        p=(root/entry['local_path']).resolve()
        if not p.is_relative_to(root.resolve()):raise EngineError('UNSAFE_SOURCE_PATH','Archive source escapes its directory.')
        actual=hashlib.sha256(p.read_bytes()).hexdigest()
        if actual!=entry['sha256']:raise EngineError('HOH_ARCHIVE_HASH_MISMATCH','Source bytes changed from the preserved manifest.')
        checks.append({'source_id':entry['source_id'],'sha256':actual,'hash_verified':True})
    with (root/'source_players.csv').open(newline='') as f:players=list(csv.DictReader(f))
    blockers=[
      {'code':'CURRENT_OWNERSHIP_MISSING','needed':'Actual selected keepers and current ownership by team; keeper candidates and computed KEEP advice do not qualify.'},
      {'code':'PICK_LEDGER_MISSING','needed':'Verified remaining pick order, current on-clock team and keeper-charged pick treatment.'},
      {'code':'CURRENT_ELIGIBILITY_STATUS_MISSING','needed':'An as-of eligibility/status snapshot; the August archive is not a live refresh.'},
      {'code':'GAME_FORECASTS_MISSING','needed':'Dated per-game schedule and coherent period projections reconciled to the chosen forecast horizon.'},
      {'code':'IR_ENTRY_AND_DRAFT_RULES_UNVERIFIED','needed':'Which statuses qualify for IR, whether IR affects draft selection capacity, and movement/lock rules for those slots.'},
      {'code':'EXACT_SCORING_TREATMENT_UNVERIFIED','needed':'Ratio precision, tie credit and undefined-rate treatment from the actual league/platform.'},
      {'code':'MANAGER_COUNTING_SEMANTICS_PENDING','needed':'Choose the counting basis and activation for the already-recorded personal G/LW/RW limits; do not reclassify them as league rules.'},
      {'code':'REFERENCE_HORIZON_NOT_ESTABLISHED','needed':'A declared reference field consistent with real projections and scoring horizon, with provenance.'}]
    return {'version':'HOH-READINESS/0.3.6','status':'BLOCKED_FOR_REAL_RECOMMENDATION','recommended_player_id':None,
      'archive_status':'SOURCE_BACKED_ARCHIVE_NOT_CURRENT_ACCOUNT_VERIFICATION','archive_claimed_date':settings['source_claimed_snapshot_date'],
      'source_player_rows':len(players),'source_hash_checks':checks,
      'known_archived_rules':{k:settings[k] for k in ['number_of_teams','objective','roster_count','ir_capacity','active_slots','bench_capacity','keeper_policy','categories','def_definition','lineup_change_frequency','player_lock_rule']},
      'known_absent_archived_rules':{'goalie_minimum':True,'weekly_position_limits':True},
      'stored_personal_preferences':settings['manager_policy_not_league_rule'],
      'technical_progress':{'scaled_position_constraints':'IMPLEMENTED_AND_TESTED','static_IR_reserve_storage':'IMPLEMENTED_WITH_EXPLICIT_STATUS_GATES','scheduled_game_lock_transitions':'OFFLINE_GUARD_IMPLEMENTED_NOT_LIVE_CONNECTED','real_ownership_inferred':False},
      'blockers':blockers,'ownership_count':None,'keeper_candidates_not_selected':True,
      'historical_inputs_ready':False,'commercial_rights_ready':False,
      'warning':'This audit verifies preserved hashes and known archive fields; it cannot authenticate actual current ownership or turn missing fields into defaults.'}

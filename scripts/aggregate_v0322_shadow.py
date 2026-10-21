#!/usr/bin/env python3
"""Aggregate 35 original v0.3.21 unconfirmed states after 2048 shadow replay."""
from __future__ import annotations

import argparse
import collections
import csv
import json
import os
from pathlib import Path

SEASONS=("2022-23","2023-24")

def atomic(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    p=path.with_name(path.name+".tmp")
    p.write_text(json.dumps(obj,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    os.replace(p,path)

def main():
    a=argparse.ArgumentParser()
    a.add_argument("--run-dir",type=Path,required=True)
    args=a.parse_args()
    root=args.run_dir.resolve()
    rows=[]
    kinds=set()
    for season in SEASONS:
        for seat in range(1,7):
            f=root/"seat_results"/f"{season}_seat{seat}.json"
            record=json.loads(f.read_text(encoding="utf-8"))
            if not record.get("complete") or (record["season"],record["seat"])!=(season,seat):
                raise RuntimeError(f"Unfinished or mismatched seat: {f}")
            kinds.add((record["source_bundle_sha256"],record["source_protocol_sha256"],
                       record["shadow_runner_sha256"],record["target_budget"]))
            for d in record["rows"]:
                if d["new_stage"]["budget"]!=2048:
                    raise RuntimeError("Missing 2048 stage evidence")
                rows.append({"season":season,"seat":seat,**d})
    if len(kinds)!=1:
        raise RuntimeError("Mixed study provenance")
    if len(rows)!=35:
        raise RuntimeError(f"Expected 35 original unconfirmed decisions; observed {len(rows)}")
    keys={(d["season"],d["seat"],d["pick_index"]) for d in rows}
    if len(keys)!=35:
        raise RuntimeError("Duplicate shadow decision key")

    status=collections.Counter(d["extended_status"] for d in rows)
    valid={"CONFIRMED_EDGE","PRACTICAL_TIE_MAX_BUDGET","UNCONFIRMED_EDGE_MAX_BUDGET"}
    if set(status)-valid:
        raise RuntimeError("Unknown result status")
    groups={}
    for was_separated in (True,False):
        subset=[d for d in rows if d["source_1024_separated"] is was_separated]
        groups[str(was_separated)]={
            "source_count":len(subset),
            "terminal_status_counts":dict(collections.Counter(x["extended_status"] for x in subset))
        }
    if sum(x["source_count"] for x in groups.values())!=35:
        raise RuntimeError("Stage 1024 strata mismatch")

    reduced=status["CONFIRMED_EDGE"]+status["PRACTICAL_TIE_MAX_BUDGET"]
    result={
        "version":"v0.3.22-2048-shadow-summary/1",
        "role":"DEVELOPMENT_DIAGNOSTIC_ONLY_NOT_MODEL_VALIDATION",
        "original_full_development_version":"v0.3.21",
        "original_unconfirmed_decisions":35,
        "original_full_development_decisions":228,
        "shadow_target_budget":2048,
        "new_status_counts":dict(status),
        "source_strata":groups,
        "leader_changed_since_1024":sum(x["leader_changed"] for x in rows),
        "shadow_reclassified_out_of_unconfirmed":reduced,
        "shadow_remaining_unconfirmed":status["UNCONFIRMED_EDGE_MAX_BUDGET"],
        "fixed_source_hypothetical_unconfirmed_rate":status["UNCONFIRMED_EDGE_MAX_BUDGET"]/228,
        "would_reduce_by_at_least_one":reduced>=1,
        "limitations":[
            "Only the prior v0.3.21 35 unconfirmed draft states are extended.",
            "Does not replay 81 practical ties, 112 confirmed edges, or changed draft trajectories.",
            "The existing 15% acceptance gate remains frozen and unpassed for v0.3.21.",
            "The shadow classification cannot approve any version for unseen validation.",
            "Opening 2024-25 or 2025-26 is prohibited pending a passing complete development study.",
        ]
    }
    atomic(root/"summary.json",result)
    fields=[
        "season","seat","round","pick_index","source_1024_separated",
        "source_1024_gap","source_1024_leader","leader_changed",
        "extended_status","elapsed_seconds",
    ]
    with (root/"decision_comparison.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        w.writeheader()
        for d in sorted(rows,key=lambda x:(x["season"],x["seat"],x["pick_index"])):
            w.writerow({k:d.get(k) for k in fields})
    print(json.dumps(result,indent=2),flush=True)

if __name__=="__main__":
    main()

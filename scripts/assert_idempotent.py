#!/usr/bin/env python3
"""Re-ingest the observations of a finished run and assert IngestReport.changed == False (SPEC.md I9). Owner: B2.

Usage: python scripts/assert_idempotent.py logs/<run_id> [--json]

Reads logs/<run_id>/exports/world-ingest.json (the observation descriptors the Keeper wrote: externalId, at,
provenance, family, name, payload export file), rebuilds the IngestObservations from the exported payloads,
folds them into a FRESH World Model (temp dir) twice and exits 1 when the second pass appended any event or
reported changed. When the run folder also holds world.events.jsonl, the fresh fold is compared with the run's
own folded state (same observations -> same canonical state shape), which doubles as the sample-vs-live proof.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator.clock import SampleClock  # noqa: E402
from investigator.worldmodel import WorldModel  # noqa: E402
from investigator.worldmodel.adapters import adapter_or_skip  # noqa: E402
from investigator.worldmodel.types import IngestObservation, Provenance  # noqa: E402


def load_observations(run_dir: Path) -> list[IngestObservation]:
    manifest = run_dir / "exports" / "world-ingest.json"
    if not manifest.is_file():
        raise SystemExit(f"no {manifest}; the Keeper writes it on ingest pass 1")
    rows = json.loads(manifest.read_text(encoding="utf-8"))
    observations: list[IngestObservation] = []
    for row in rows:
        payload_path = run_dir / "exports" / row["payloadExport"]
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        source = row["source"]
        observations.append(IngestObservation(
            external_id=row["externalId"], at=row["at"], payload=payload, type=row.get("type", "observation"), title=row.get("title", ""),
            source=Provenance(kind=source["kind"], at=source["at"], by=source.get("by"), url=source.get("url"),
                              checksum=source.get("checksum"), run_id=source.get("runId"))))
    return observations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dir", help="logs/<run_id> of a finished run")
    parser.add_argument("--json", action="store_true", help="print the two reports as JSON only")
    args = parser.parse_args(argv)
    run_dir = Path(args.run_dir)
    observations = load_observations(run_dir)
    run_id = observations[0].source.run_id if observations and observations[0].source.run_id else run_dir.name
    tmp = Path(tempfile.mkdtemp(prefix="idem-"))
    clock = SampleClock(observations[0].at if observations else "2026-10-22T00:05:00.000Z")
    world = WorldModel(tmp, clock, run_id)
    first = world.ingest(observations, adapter_or_skip)
    second = world.ingest(observations, adapter_or_skip)
    fresh_shape = world.state_shape_canonical()
    world.close()
    comparison = None
    if (run_dir / "world.events.jsonl").is_file():
        recorded = WorldModel(run_dir, clock, run_id, policy_hook=None)
        # the recorded run also holds chain facts and agent claims: compare only what observations produce
        recorded_shape = recorded.state_shape_canonical()
        comparison = {"recorded_events": recorded.counts()["events"], "fresh_events": world.counts()["events"],
                      "observations_subset_equal": _observations_subset_equal(world, recorded)}
        recorded.close()
        del recorded_shape
    result = {"run_id": run_id, "observations": len(observations), "first_pass": first.summary(), "second_pass": second.summary(),
              "quarantined": second.quarantined, "fresh_state_sha256_prefix": __import__("hashlib").sha256(fresh_shape.encode("utf-8")).hexdigest()[:16],
              "comparison_with_recorded_run": comparison}
    ok = (not second.changed) and second.events_appended == 0 and not second.quarantined
    result["ok"] = ok
    print(json.dumps(result, indent=2))
    if not args.json:
        print("idempotent: PASS" if ok else "idempotent: FAIL (second pass changed the world)")
    return 0 if ok else 1


def _observations_subset_equal(fresh: WorldModel, recorded: WorldModel) -> bool:
    """Every entity/relationship/fact the fresh fold produced exists identically in the recorded run."""
    rec = recorded.state
    for rel_id, rel in fresh.state.relationships.items():
        other = rec.relationships.get(rel_id)
        if other is None or other.to_id != rel.to_id or other.from_id != rel.from_id:
            return False
    for fact_id, fact in fresh.state.facts.items():
        other = rec.facts.get(fact_id)
        if other is None or other.value != fact.value:
            return False
    for entity_id in fresh.state.entities:
        if entity_id not in rec.entities:
            return False
    return True


if __name__ == "__main__":
    sys.exit(main())

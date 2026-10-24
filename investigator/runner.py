"""Deterministic state machine: the pipeline, budgets, deadline, handoffs and finalize (SPEC.md sections 4-6). Owner: B4.

    planner -> collector -> keeper(ingest pass 1) -> verifier -> keeper(chain facts) -> analyst
    -> [writer -> critic] x <= max_critic_rounds -> keeper(assert accepted claims; second-pass proof)
    -> hmem(feedback, mining, sleep preview) -> finalize(report.md, output.json)

The runner never retries the whole run and never swallows an exception into `partial`: an uncaught exception is
E_INTERNAL naming the stage and the last log seq, after flushing the logs. In sample mode the first run.jsonl
line is the `guard` event (network disabled). Exactly three mount points differ between modes (fetcher,
executor, generator) and they are the first key of output.json (`mounts`). In live mode the generator is Meterless
Nightshift when its runtime is online, else the OpenAI-compatible fallback, else rules and templates; a live run
that does not mount Nightshift says why in recovery{nightshift_unavailable}. The Meterless engines (H-MEM, World
Model) are mounted on the context when their modules are implemented; until then the run says so honestly in
the agents' notes and `output.memory` / `output.world` report what was skipped.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import __version__
from .agents import analyst, collector, critic, keeper, planner, report, verifier, writer
from .agents.verdict import Verdict, compute_verdict
from .agents.writer import Answer, verdict_sentence
from .clock import Clock, SampleClock, SystemClock
from .config import RunConfig, parse_request
from .contracts import AgentResult, Budget, Claim, Conflict, Mounts, Observation, Plan, RunInput, to_json
from .errors import InvestigatorError
from .eventlog import EventLog
from .generator import build_generator
from .guard import NetworkDisabledError, install_network_guard
from .sources.fetcher import build_fetcher, fixture_fetcher_for
from .sources.manifest import Manifest, load_manifest
from .zetaris.executor import build_executor

STAGES: tuple[str, ...] = ("planner", "collector", "keeper", "verifier", "analyst", "writer", "critic", "hmem", "finalize")
GENERATOR_BACKED: frozenset[str] = frozenset({"planner", "writer", "critic"})


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


VOLATILE_KEYS: frozenset[str] = frozenset({"export_path", "raw_path"})   # machine paths: never part of an artifact hash


def _strip_volatile(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_volatile(v) for k, v in obj.items() if k not in VOLATILE_KEYS}
    if isinstance(obj, list):
        return [_strip_volatile(v) for v in obj]
    return obj


def sha256_json(obj: Any) -> str:
    return sha256_text(json.dumps(_strip_volatile(to_json(obj)), sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str))


def wall_seconds() -> float:
    """Monotonic wall time in seconds. budget.max_seconds is measured with it in both modes (tests patch it)."""
    return time.monotonic()


@dataclass
class RunContext:
    """Everything an agent needs; passed to every agent entry point."""

    run_id: str
    config: RunConfig
    inp: RunInput
    mounts: Mounts
    clock: Clock
    log: EventLog
    run_dir: Path
    fetcher: Any
    executor: Any
    generator: Any
    manifest: Optional[Manifest] = None
    hmem: Any = None            # investigator.hmem.HMEM when the engine is mounted
    world: Any = None           # investigator.worldmodel.WorldModel when the engine is mounted
    hmem_dir: Optional[Path] = None
    example_name: Optional[str] = None
    start_ms: int = 0
    start_iso: str = ""
    deadline_ms: int = 0
    wall_start_s: Optional[float] = None   # wall_seconds() at run start; budget.max_seconds is a wall-clock bound
    stage: str = "init"
    counters: dict[str, int] = field(default_factory=lambda: {
        "fetches": 0, "sql": 0, "generator_calls": 0, "tx_verifications": 0, "critic_rounds": 0, "deadline_skips": 0})
    recoveries: list[dict[str, Any]] = field(default_factory=list)
    fallback_used: bool = False     # a fallback mount was used against the requested mode
    results: list[AgentResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)          # runner-level notes (mount degradations)
    scratch: dict[str, Any] = field(default_factory=dict)   # cross-agent working data (recompute table, retrievals)

    @property
    def budget(self) -> Budget:
        return self.inp.budget

    @property
    def exports_dir(self) -> Path:
        path = self.run_dir / "exports"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def raw_dir(self) -> Path:
        path = self.run_dir / "raw"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def wall_elapsed_ms(self) -> Optional[int]:
        return None if self.wall_start_s is None else int((wall_seconds() - self.wall_start_s) * 1000)

    def time_left_ms(self) -> int:
        """Milliseconds left of budget.max_seconds: the smaller of the run clock's and the monotonic wall clock's view.
        In sample mode the run clock is the deterministic SampleClock (1 ms per call), which never reaches the
        deadline by itself, so the wall clock is what bounds a sample run; nothing logged depends on it until it fires."""
        left = self.deadline_ms - self.clock.now_ms()
        wall = self.wall_elapsed_ms()
        if wall is not None:
            left = min(left, self.budget.max_seconds * 1000 - wall)
        return left

    def deadline_hit(self, stage: str) -> bool:
        """True (and a `timeout` event) when the deadline has passed."""
        left = self.time_left_ms()
        if left > 0:
            return False
        elapsed_s = round((self.clock.now_ms() - self.start_ms) / 1000, 3)
        payload: dict[str, Any] = {"stage": stage, "elapsed_s": elapsed_s, "budget_s": self.budget.max_seconds}
        wall = self.wall_elapsed_ms()
        if wall is not None:
            payload["wall_elapsed_s"] = round(wall / 1000, 3)
        self.log.emit("timeout", "runner", payload)
        return True

    def recover(self, agent: str, reason: str, detail: str, **extra: Any) -> int:
        payload = {"reason": reason, "detail": detail, **extra}
        seq = self.log.emit("recovery", agent, payload)
        self.recoveries.append({"seq": seq, **payload})
        if reason == "deadline_skip":
            self.counters["deadline_skips"] += 1
        return seq

    def budget_take(self, counter: str, limit: int, agent: str) -> bool:
        """Consume one unit of a bounded budget; False (and a recovery) when exhausted."""
        if self.counters.get(counter, 0) >= limit:
            self.recover(agent, "budget_exhausted", f"{counter} limit {limit} reached", counter=counter, limit=limit)
            return False
        self.counters[counter] = self.counters.get(counter, 0) + 1
        return True

    def handoff(self, src: str, dst: str, artifact_type: str, items: Any) -> int:
        count = len(items) if isinstance(items, (list, dict, tuple)) else (1 if items is not None else 0)
        return self.log.emit("handoff", "runner", {
            "from": src, "to": dst, "artifact": {"type": artifact_type, "count": count, "sha256": sha256_json(items)}})

    def record(self, result: AgentResult) -> AgentResult:
        """Keep every AgentResult for the agents[] summary and the status rule."""
        self.results.append(result)
        return result


# ----------------------------------------------------------------------------------------------------------
# Mounting
# ----------------------------------------------------------------------------------------------------------

def find_example_name(config: RunConfig, run_id: str) -> Optional[str]:
    """Map a pinned run_id to its examples/<folder> (for recorded transcripts)."""
    examples_dir = config.repo_root / "examples"
    if not examples_dir.is_dir():
        return None
    for folder in sorted(examples_dir.iterdir()):
        input_path = folder / "input.json"
        if folder.is_dir() and input_path.is_file():
            try:
                if json.loads(input_path.read_text(encoding="utf-8")).get("run_id") == run_id:
                    return folder.name
            except (OSError, ValueError):
                continue
    return None


def init_hmem_dir(config: RunConfig, run_dir: Path) -> Path:
    """Sample mode: copy memory/seed into logs/<run_id>/hmem/. Live mode: HMEM_DIR, seeded when empty."""
    target = run_dir / "hmem" if config.is_sample() else config.env.hmem_dir
    target.mkdir(parents=True, exist_ok=True)
    for name in ("memories.jsonl", "ledger.jsonl"):
        src = config.seed_dir / name
        dst = target / name
        if src.is_file() and (config.is_sample() or not dst.exists()):
            shutil.copyfile(src, dst)
    return target


def mount_engines(ctx: RunContext) -> None:
    """Mount H-MEM and the World Model when their implementations are available (never raises)."""
    try:
        from .hmem import HMEM
        hmem = HMEM(ctx.clock, ctx.hmem_dir or (ctx.run_dir / "hmem"), actor="runner")
        hmem.load()
        ctx.hmem = hmem
    except NotImplementedError as exc:
        ctx.hmem = None
        ctx.notes.append(f"stub: H-MEM engine not mounted ({exc})")
    except Exception as exc:  # noqa: BLE001 - an engine that fails to load is reported, not hidden
        ctx.hmem = None
        ctx.notes.append(f"H-MEM engine failed to mount: {type(exc).__name__}: {exc}")
    try:
        from .worldmodel import WorldModel
        ctx.world = WorldModel(ctx.run_dir, ctx.clock, ctx.run_id)
    except NotImplementedError as exc:
        ctx.world = None
        ctx.notes.append(f"stub: World Model engine not mounted ({exc})")
    except Exception as exc:  # noqa: BLE001
        ctx.world = None
        ctx.notes.append(f"World Model engine failed to mount: {type(exc).__name__}: {exc}")


def reset_run_dir(out_dir: Path, run_id: str) -> Path:
    """logs/<run_id>/ names ONE run: a re-run with the same run_id replaces the previous files instead of
    appending to them (the log seq restarts at 1, so appended files would make log://<run_id>#<seq> ambiguous).
    Only a direct child of the logs directory is ever removed."""
    out_dir = Path(out_dir)
    run_dir = out_dir / run_id
    if run_dir.exists():
        resolved = run_dir.resolve()
        if resolved.parent == out_dir.resolve() and resolved != out_dir.resolve() and run_dir.is_dir():
            shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def build_context(run_id: str, inp: RunInput, config: RunConfig, echo_stdout: bool) -> RunContext:
    run_dir = reset_run_dir(Path(config.out_dir), run_id)
    manifest = load_manifest(config.manifest_path) if config.manifest_path.is_file() else None
    captured_at = manifest.fetched_at if manifest else None
    clock: Clock = SampleClock(captured_at or "2026-10-22T00:05:00Z") if config.is_sample() else SystemClock()
    log = EventLog(run_id, run_dir, clock, echo_stdout=echo_stdout)

    if config.is_sample():
        patched = install_network_guard()
        log.emit("guard", "runner", {"network": "disabled", "patched": patched})

    example_name = config.example_name or find_example_name(config, run_id)
    fetcher = build_fetcher(config, manifest, clock, run_dir, example_name)
    try:
        executor = build_executor(config, run_dir, log, run_id, zetaris_source=inp.options.get("zetaris_source"))
    except TypeError:
        executor = build_executor(config, run_dir, log, run_id)
    if config.mode == "live" and config.require_zetaris and not config.env.has_zetaris():
        raise InvestigatorError("E_ZETARIS_REQUIRED", "mounts", "Zetaris credentials are not configured and --require-zetaris is set",
                                retryable=True, details={"reason": "zetaris_required"})
    generator = build_generator(config, example_name)   # live: Nightshift (probed) -> OpenAI-compatible -> null
    nightshift_unavailable = getattr(generator, "nightshift_unavailable", None)
    if config.mode == "live" and config.require_generator and generator.name == "null":
        why = nightshift_unavailable["detail"] if nightshift_unavailable else "Nightshift not online"
        raise InvestigatorError("E_GENERATOR", "mounts", f"no generator is available and --require-generator is set: {why}; "
                                "OPENAI_API_KEY (fallback) is not set", retryable=True,
                                details={"reason": "generator_required",
                                         "nightshift": nightshift_unavailable["status"] if nightshift_unavailable else None})

    zetaris_source = inp.options.get("zetaris_source") if config.mode == "live" else None
    if config.zetaris_source and config.mode == "live":
        zetaris_source = config.zetaris_source
    mounts = Mounts(
        mode=config.mode,
        fetcher=fetcher.mode,
        executor=executor.name,
        generator=generator.name,
        fixtures_captured_at=captured_at if (config.is_sample() or fetcher.mode != "live") else None,
        zetaris_version=getattr(executor, "version", None) if executor.name == "zetaris" else None,
        zetaris_source=zetaris_source if executor.name == "zetaris" else None,
    )
    ctx = RunContext(run_id=run_id, config=config, inp=inp, mounts=mounts, clock=clock, log=log, run_dir=run_dir,
                     fetcher=fetcher, executor=executor, generator=generator, manifest=manifest, example_name=example_name)
    if config.mode == "live" and executor.name != "zetaris":
        ctx.fallback_used = True
        reason = getattr(executor, "fallback_reason", None) or "Zetaris not configured"
        ctx.recover("runner", "zetaris_unreachable", f"{reason}; whole run on SQLite", to="sqlite")
    if config.mode == "live" and nightshift_unavailable:
        # Nightshift is the primary provider: say why it is not mounted and what answers instead. With the
        # OpenAI-compatible fallback a model still runs, so this alone does not make the run partial.
        ctx.recover("runner", "nightshift_unavailable", nightshift_unavailable["detail"], **{"from": "nightshift"},
                    to=generator.name, status=nightshift_unavailable["status"], base_url=nightshift_unavailable["base_url"])
    if config.mode == "live" and generator.name == "null":
        ctx.fallback_used = True
        ctx.recover("runner", "generator_fallback", "no generator configured; rules and templates only", to="null")
    ctx.hmem_dir = init_hmem_dir(config, run_dir)
    mount_engines(ctx)
    return ctx


# ----------------------------------------------------------------------------------------------------------
# Pipeline
# ----------------------------------------------------------------------------------------------------------

def _payload_dict(result: AgentResult) -> dict[str, Any]:
    return result.payload if isinstance(result.payload, dict) else {}


def run_pipeline(ctx: RunContext) -> dict[str, Any]:
    """Execute the fixed pipeline and return the `output` object (not the envelope)."""
    inp = ctx.inp
    budget = ctx.budget

    # planner
    ctx.stage = "planner"
    ctx.handoff("runner", "planner", "input", [inp.question])
    plan_result = ctx.record(planner.run_planner(ctx))
    plan: Plan = plan_result.payload
    ctx.log.emit("plan", "planner", {
        "playbook": plan.playbook, "query_ids": list(plan.query_ids), "sources": plan.source_families(),
        "fetch_count": len(plan.fetch_list), "hmem_memory_ids": list(plan.hmem_memory_ids),
        "planner_source": plan.planner_source, "metrics": list(plan.metrics), "hypotheses": list(plan.hypotheses)})

    # collector
    ctx.stage = "collector"
    ctx.handoff("planner", "collector", "plan", plan.fetch_list)
    collector_result = ctx.record(collector.run_collector(ctx, plan))
    observations: list[Observation] = list(collector_result.payload or [])
    if collector_result.status == "failed":
        if ctx.counters.get("deadline_skips", 0) > 0:
            raise InvestigatorError("E_TIMEOUT", "collector", "deadline hit before any source was fetched",
                                    retryable=True, details={"elapsed_ms": ctx.clock.now_ms() - ctx.start_ms,
                                                             "wall_elapsed_ms": ctx.wall_elapsed_ms(),
                                                             "notes": collector_result.notes})
        raise InvestigatorError("E_NO_SOURCES", "collector", "every mandatory source is unavailable",
                                retryable=True, details={"notes": collector_result.notes})

    # keeper pass 1
    ctx.stage = "keeper"
    ctx.handoff("collector", "keeper", "observations", observations)
    keeper1 = ctx.record(keeper.run_keeper_ingest(ctx, observations, pass_no=1))

    # verifier
    ctx.stage = "verifier"
    ctx.handoff("keeper", "verifier", "world", [keeper1.payload] if keeper1.payload else [])
    if ctx.deadline_hit("verifier"):
        ctx.recover("verifier", "deadline_skip", "chain verification skipped: deadline")
        verifier_result = AgentResult("verifier", "degraded", verifier.empty_payload(), ["deadline_skip"])
        ctx.results.append(verifier_result)
    else:
        verifier_result = ctx.record(verifier.run_verifier(ctx, plan, observations))
    vpay = _payload_dict(verifier_result)
    verifications = list(vpay.get("verifications", []))
    chain_facts = list(vpay.get("chain_facts", []))
    verifier_conflicts: list[Conflict] = list(vpay.get("conflicts", []))
    rpc_observations: list[Observation] = [o for o in vpay.get("observations", []) if isinstance(o, Observation)]

    # keeper chain facts
    ctx.stage = "keeper"
    ctx.handoff("verifier", "keeper", "chain_facts", chain_facts)
    ctx.record(keeper.run_keeper_chain_facts(ctx, chain_facts))

    # analyst
    ctx.stage = "analyst"
    ctx.handoff("keeper", "analyst", "query_ids", plan.query_ids)
    if ctx.deadline_hit("analyst"):
        ctx.recover("analyst", "deadline_skip", "analysis skipped: deadline")
        analyst_result = AgentResult("analyst", "degraded", {"claims": [], "conflicts": [], "unavailable": [
            {"metric": m, "reason": "deadline hit before the Analyst ran"} for m in plan.metrics]}, ["deadline_skip"])
        ctx.results.append(analyst_result)
    else:
        analyst_result = ctx.record(analyst.run_analyst(ctx, plan, observations + rpc_observations, verifications,
                                                        prior_conflicts=verifier_conflicts, chain_facts=chain_facts))
    apay = _payload_dict(analyst_result)
    claims: list[Claim] = list(apay.get("claims", []))
    conflicts: list[Conflict] = verifier_conflicts + list(apay.get("conflicts", []))
    unavailable: list[dict[str, Any]] = list(apay.get("unavailable", []))
    ctx.scratch["conflicts"] = conflicts

    # writer <-> critic loop (bounded)
    rounds = 0
    issues: list[dict[str, Any]] = []
    answer: Optional[Answer] = None
    critic_payload: Optional[critic.CriticResult] = None
    max_rounds = max(1, budget.max_critic_rounds)
    while rounds < max_rounds:
        rounds += 1
        ctx.counters["critic_rounds"] = rounds
        ctx.stage = "writer"
        ctx.handoff("analyst" if rounds == 1 else "critic", "writer", "claims" if rounds == 1 else "issues",
                    claims if rounds == 1 else issues)
        if ctx.deadline_hit("writer"):
            ctx.recover("writer", "deadline_skip", f"round {rounds} skipped: deadline")
            break
        provisional = compute_verdict(plan.playbook, [c for c in claims if c.status != "rejected"])
        writer_result = ctx.record(writer.run_writer(ctx, claims, conflicts, unavailable, provisional, issues, rounds))
        answer = writer_result.payload
        ctx.stage = "critic"
        ctx.handoff("writer", "critic", "answer", answer.sentences)
        critic_result = ctx.record(critic.run_critic(ctx, answer, claims, verifications, observations, rounds,
                                                     conflicts=conflicts, verdict_confidence=provisional.confidence))
        critic_payload = critic_result.payload
        if not critic_payload.revise:
            break
        issues = list(critic_payload.issues)
        # The next round is a revision driven by the Critic's issues (the `handoff` critic -> writer carries them),
        # not a retry of a failed operation: `retry` events are reserved for retried requests (the Fetcher's retry
        # policy, sources/retry.py) and the Planner's one re-ask after an invalid generator reply.
    finalize_claims(ctx, claims, rounds)
    if answer is None:
        answer = writer.render_template(claims, conflicts, unavailable, compute_verdict(plan.playbook, []), None, [], False, rounds)
    elif critic_payload is not None and critic_payload.revise:
        answer = writer.trim_unsourced(ctx, answer, critic_payload, claims)
    # a generated draft (or a trim) can leave out code-owned sentences; a template answer carries them all already
    answer = writer.complete_requirements(ctx, answer, critic_payload, claims, conflicts, unavailable)

    accepted = [c for c in claims if c.status == "accepted"]
    rejected = [c for c in claims if c.status == "rejected"]
    verdict: Verdict = compute_verdict(plan.playbook, accepted)
    if answer.verdict != verdict.label and answer.sentences:
        answer.sentences[0] = verdict_sentence(verdict)
        answer.verdict = verdict.label
        ctx.recover("writer", "narrative_trimmed", f"verdict sentence rewritten: final verdict {verdict.label} differs from the draft")
    elif verdict.label not in answer.text:
        # the draft never named the verdict label, or the trim cut the sentence that did: state it (code-owned)
        answer.sentences.insert(0, verdict_sentence(verdict))
        ctx.recover("writer", "narrative_completed", f"verdict sentence added: the final answer did not state {verdict.label}",
                    sentences=[answer.sentences[0][:160]], added={"verdict": verdict.label})

    # keeper: assert accepted claims + second pass
    ctx.stage = "keeper"
    ctx.handoff("critic", "keeper", "accepted_claims", accepted)
    keeper3 = ctx.record(keeper.run_keeper_claims(ctx, accepted, observations))
    counts = None
    if ctx.world is not None and hasattr(ctx.world, "counts"):
        try:
            counts = ctx.world.counts()
        except Exception:  # noqa: BLE001
            counts = None
    try:
        world_summary = keeper.world_summary(keeper1.payload, keeper3.payload, counts)
    except TypeError:
        world_summary = keeper.world_summary(keeper1.payload, keeper3.payload)

    # hmem finalize
    ctx.stage = "hmem"
    ctx.handoff("keeper", "hmem", "claims", accepted)
    memory_summary = finalize_memory(ctx, accepted, plan, answer)

    # finalize
    ctx.stage = "finalize"
    ctx.handoff("hmem", "finalize", "output", [verdict.label])
    stub_notes = [n for r in ctx.results for n in r.notes if n.startswith("stub:")] + [n for n in ctx.notes if n.startswith("stub:")]
    output: dict[str, Any] = {"mounts": to_json(ctx.mounts)}
    if ctx.config.is_sample():
        captured = ctx.mounts.fixtures_captured_at or "unknown date"
        output["sample_mode_notice"] = (
            f"This run replayed committed fixtures captured {captured}; no network, no model calls.")
        replayed = ctx.scratch.get("generator_calls", [])
        if replayed and all(c.get("recorded") for c in replayed):
            providers = ", ".join(sorted({str(c.get("provider")) for c in replayed}))
            models = ", ".join(sorted({str((c.get("routing") or {}).get("routed_model") or c.get("model")) for c in replayed}))
            output["sample_mode_notice"] += (
                f" The generator-backed roles replayed {len(replayed)} recorded answer(s) from fixtures/transcripts/"
                f"{ctx.config.example_name or ctx.example_name}/ (provider {providers}, model {models}); each `generate` "
                "event says recorded: true.")
        faults = getattr(fixture_fetcher_for(ctx.fetcher), "faults", None)
        if faults is not None:
            origin = ("simulated: written by hand, not captured from the sources" if faults.simulated
                      else "captured from the sources")
            output["sample_mode_notice"] += (
                f" It also replayed the {faults.count()} transient failure(s) scripted in {faults.rel_path} ({origin}) "
                "through the retry policy live runs use; the `retry` events in run.jsonl show each one.")
    if stub_notes:
        output["scaffold_notice"] = (
            "SCAFFOLD: some modules are still stubs, so parts of this run were skipped and are reported as such. "
            "Stubs: " + " | ".join(sorted(set(stub_notes))))
    output["question"] = inp.question
    output["plan"] = {"playbook": plan.playbook, "query_ids": list(plan.query_ids),
                      "sources": plan.source_families(), "planner_source": plan.planner_source,
                      "metrics": list(plan.metrics), "hypotheses": list(plan.hypotheses),
                      "hmem_memory_ids": list(plan.hmem_memory_ids)}
    output["verdict"] = to_json(verdict)
    output["answer"] = answer.text
    output["claims"] = [to_json(c) for c in accepted]
    output["rejected_claims"] = [to_json(c) for c in rejected]
    output["conflicts"] = [to_json(k) for k in conflicts]
    output["unavailable"] = unavailable
    output["sources"] = [collector.source_summary(o) for o in observations + rpc_observations]
    output["verifications"] = [to_json(v) for v in verifications]
    output["world"] = world_summary
    output["memory"] = memory_summary
    output["agents"] = agent_summaries(ctx)
    output["critique_rounds"] = rounds
    output["report_path"] = f"logs/{ctx.run_id}/report.md"
    output["log_files"] = ctx.log.files_written()
    return output


def finalize_claims(ctx: RunContext, claims: list[Claim], rounds: int) -> None:
    """After the last round: claims still `revised`/`proposed` were never re-checked clean -> rejected."""
    for claim in claims:
        if claim.status in ("revised", "proposed"):
            reason = (f"unresolved after {rounds} critic round(s): revised but not re-checked" if claim.status == "revised"
                      else "never reviewed by the Critic (deadline or round limit)")
            claim.critique.append(critic.Critique(round=rounds, rule="A", verdict="reject", reason=reason))
            ctx.log.emit("critique", "critic", {"round": rounds, "rule": "A", "target": claim.id, "verdict": "reject",
                                                "reason": reason, "memory_ids": []})
            claim.status = "rejected"


def finalize_memory(ctx: RunContext, accepted: list[Claim], plan: Plan, answer: Answer) -> dict[str, Any]:
    """H-MEM finalize: feedback per memory, mining of accepted claims, sleep preview (SPEC.md section 8.3)."""
    retrieved = list(ctx.scratch.get("retrievals", []))
    cited = sorted({m for c in accepted for m in c.caveat_memory_ids.values()} | set(answer.memory_ids_cited))
    feedback_plan: dict[str, str] = dict(ctx.scratch.get("memory_feedback", {}))
    reasons: dict[str, str] = dict(ctx.scratch.get("memory_feedback_reasons", {}))
    for item in retrieved:
        memory_id = item["memory_id"]
        if memory_id not in feedback_plan and memory_id not in cited:
            feedback_plan[memory_id] = "not_helpful"
            reasons[memory_id] = f"retrieved for {item['actor']} but not cited"
    summary: dict[str, Any] = {"retrieved": retrieved, "cited": cited, "feedback": [], "mined": 0}
    hmem = ctx.hmem
    if hmem is None:
        ctx.log.emit("memory", "runner", {"op": "sleep_preview", "actor": "runner", "memory_ids": [],
                                          "details": {"skipped": "H-MEM not mounted"}})
        summary["planned_feedback"] = [{"memory_id": m, "kind": k, "reason": reasons.get(m, "")} for m, k in sorted(feedback_plan.items())]
        return summary
    for memory_id, kind in sorted(feedback_plan.items()):
        try:
            before = hmem.store.get(memory_id)
            previous = float(before.confidence) if before is not None else None
            record = hmem.feedback(memory_id, kind, actor="critic", reason=reasons.get(memory_id, ""))
            new = float(record.confidence)
        except NotImplementedError as exc:
            ctx.notes.append(f"stub: H-MEM feedback unavailable ({exc})")
            summary["planned_feedback"] = [{"memory_id": m, "kind": k, "reason": reasons.get(m, "")} for m, k in sorted(feedback_plan.items())]
            break
        except Exception as exc:  # noqa: BLE001
            ctx.notes.append(f"H-MEM feedback failed for {memory_id}: {type(exc).__name__}: {exc}")
            continue
        seq = ctx.log.emit("memory", "critic", {"op": "feedback", "actor": "critic", "memory_ids": [memory_id],
                                                "details": {"kind": kind, "reason": reasons.get(memory_id, ""),
                                                            "previous_confidence": previous, "new_confidence": new}})
        ctx.log.side("hmem.trace", {"seq": seq, "actor": "critic", "memoryId": memory_id, "kind": kind,
                                    "previous_confidence": previous, "new_confidence": new})
        summary["feedback"].append({"memory_id": memory_id, "kind": kind, "reason": reasons.get(memory_id, "")})
    if accepted:
        try:
            from .hmem.types import MemoryProvenance
            text = "\n".join(f"{c.statement} (metric {c.metric}; subject {c.subject})" for c in accepted)
            provenance = MemoryProvenance(origin="goal_run", learned_at=ctx.clock.now_ms(), label=ctx.inp.question,
                                          refs={"goalRunId": ctx.run_id})
            records = hmem.mining.mine("plan_completion", text, source=f"run:{ctx.run_id}", goal_run_id=ctx.run_id,
                                       provenance=provenance)
            ids = [r.id for r in records]
            summary["mined"] = len(ids)
            ctx.log.emit("memory", "runner", {"op": "mine", "actor": "runner", "memory_ids": ids,
                                              "details": {"event_type": "plan_completion", "claims": len(accepted)}})
        except NotImplementedError as exc:
            ctx.notes.append(f"stub: H-MEM mining unavailable ({exc})")
        except Exception as exc:  # noqa: BLE001
            ctx.notes.append(f"H-MEM mining failed: {type(exc).__name__}: {exc}")
    try:
        preview = hmem.sleep.preview()
        details = {k: (len(v) if isinstance(v, list) else v) for k, v in (preview or {}).items()}
        ctx.log.emit("memory", "runner", {"op": "sleep_preview", "actor": "runner", "memory_ids": [], "details": details})
        if ctx.config.mode == "live" and ctx.config.hmem_sleep:
            executed = hmem.sleep.execute(preview)
            ctx.log.emit("memory", "runner", {"op": "sleep_preview", "actor": "runner", "memory_ids": [],
                                              "details": {"executed": True, "backup_id": executed.get("backup_id")}})
    except NotImplementedError as exc:
        ctx.log.emit("memory", "runner", {"op": "sleep_preview", "actor": "runner", "memory_ids": [],
                                          "details": {"skipped": f"not implemented: {exc}"}})
    except Exception as exc:  # noqa: BLE001
        ctx.notes.append(f"H-MEM sleep preview failed: {type(exc).__name__}: {exc}")
    return summary


def agent_summaries(ctx: RunContext) -> list[dict[str, Any]]:
    order = ["planner", "collector", "keeper", "verifier", "analyst", "writer", "critic"]
    summaries: list[dict[str, Any]] = []
    calls = ctx.scratch.get("generator_calls", [])
    for role in order:
        results = [r for r in ctx.results if r.agent == role]
        if not results:
            continue
        status = "failed" if any(r.status == "failed" for r in results) else (
            "degraded" if any(r.status == "degraded" for r in results) else "ok")
        notes = sorted({n for r in results for n in r.notes})
        used = None
        if role in GENERATOR_BACKED:
            used = ctx.generator.name if any(c["agent"] == role for c in calls) else "none"
        summary: dict[str, Any] = {"role": role, "generator_backed": role in GENERATOR_BACKED,
                                   "generator_used": used, "attempts": len(results), "status": status, "notes": notes}
        role_calls = [generator_call_summary(c) for c in calls if c["agent"] == role]
        if role_calls:
            summary["generator_calls"] = role_calls
        summaries.append(summary)
    return summaries


def generator_call_summary(line: dict[str, Any]) -> dict[str, Any]:
    """One answered generator call as output.agents[].generator_calls[] shows it: which provider and model handled
    it, the Nightshift route and receipt id when there is one, and the run.jsonl seq of its generate event."""
    routing = line.get("routing") or {}
    route = routing.get("winning_route") or {}
    return {"prompt_id": line["prompt_id"], "provider": line["provider"], "requested_model": line.get("requested_model"),
            "model": routing.get("routed_model") or line["model"], "route": route.get("label"),
            "route_kind": route.get("kind"), "receipt_id": routing.get("receipt_id"), "recorded": bool(line["recorded"]),
            "log_seq": line["seq"]}


def scrub_paths(text: str, repo_root: Path) -> str:
    """Replace the repository root and the home directory with <repo> and ~ (both slash styles), so a traceback
    written to logs/<run_id>/traceback.txt carries no machine-specific absolute path."""
    for prefix, label in ((repo_root.resolve(), "<repo>"), (Path.home(), "~")):
        for form in (str(prefix), prefix.as_posix()):
            if form and form not in (".", "/"):
                text = text.replace(form, label)
    return text


def compute_status(ctx: RunContext, accepted: list[Any], rejected: list[Any], unavailable: list[Any]) -> str:
    degraded = any(r.status in ("degraded", "failed") for r in ctx.results)
    issues = bool(unavailable) or ctx.counters.get("deadline_skips", 0) > 0 or bool(rejected) or ctx.fallback_used or degraded
    if not accepted:
        return "error"
    return "partial" if issues else "ok"


def write_output(ctx: RunContext, envelope: dict[str, Any]) -> Path:
    path = ctx.run_dir / "output.json"
    path.write_text(json.dumps(envelope, indent=2, sort_keys=False, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return path


# ----------------------------------------------------------------------------------------------------------
# Entry
# ----------------------------------------------------------------------------------------------------------

def run(payload: Any, config: RunConfig, run_id_override: Optional[str] = None, echo_stdout: bool = True) -> dict[str, Any]:
    """{run_id?, input} -> {run_id, status, output} | {run_id, status: "error", error}. Never raises for a bad
    input; raises only on programming errors outside the pipeline (none expected)."""
    try:
        run_id, inp = parse_request(payload, run_id_override)
    except InvestigatorError as err:
        rid = run_id_override or (payload.get("run_id") if isinstance(payload, dict) else None) or "invalid-input"
        return {"run_id": str(rid)[:64], "status": "error", "error": err.to_json()}

    ctx: Optional[RunContext] = None
    try:
        ctx = build_context(run_id, inp, config, echo_stdout)
        ctx.wall_start_s = wall_seconds()
        ctx.start_ms = ctx.clock.now_ms()
        ctx.start_iso = ctx.clock.now_iso() if not isinstance(ctx.clock, SampleClock) else ctx.clock.start_iso
        ctx.deadline_ms = ctx.start_ms + inp.budget.max_seconds * 1000
        input_sha = sha256_json(to_json(inp))
        start_payload: dict[str, Any] = {
            "mounts": to_json(ctx.mounts), "input_sha256": input_sha, "budget": to_json(inp.budget),
            "deadline_at": ctx.clock.now_iso(), "version": __version__, "engines": {
                "hmem": ctx.hmem is not None, "world_model": ctx.world is not None}}
        mount_info = getattr(ctx.generator, "mount_info", None)
        if mount_info:   # Nightshift: where it answered, how it was found, the routing mode (never the token)
            start_payload["generator_mount"] = mount_info
        ctx.log.emit("run_start", "runner", start_payload)
        output = run_pipeline(ctx)
        status = compute_status(ctx, output["claims"], output["rejected_claims"], output["unavailable"])
        if status == "error":
            if ctx.counters.get("deadline_skips", 0) > 0:
                raise InvestigatorError("E_TIMEOUT", ctx.stage, "deadline hit before any claim was accepted",
                                        retryable=True, details={"elapsed_ms": ctx.clock.now_ms() - ctx.start_ms,
                                                                 "wall_elapsed_ms": ctx.wall_elapsed_ms()})
            if output["unavailable"] and not output["rejected_claims"]:
                raise InvestigatorError("E_NO_SOURCES", "analyst", "no claim could be computed: every metric's sources were unavailable",
                                        retryable=True, details={"unavailable": output["unavailable"][:10],
                                                                 "notes": [n for r in ctx.results for n in r.notes][:10]})
            raise InvestigatorError("E_INTERNAL", ctx.stage, "no claim was accepted",
                                    details={"rejected": [c["id"] for c in output["rejected_claims"]][:25],
                                             "recoveries": ctx.recoveries[-5:], "last_log_seq": ctx.log.seq})
        envelope = {"run_id": run_id, "status": status, "output": output}
        write_output(ctx, envelope)
        (ctx.run_dir / "report.md").write_text(report.render_report(envelope), encoding="utf-8", newline="\n")
        ctx.log.emit("result", "runner", {
            "status": status, "accepted": len(output["claims"]), "rejected": len(output["rejected_claims"]),
            "conflicts": len(output["conflicts"]), "unavailable": len(output["unavailable"]),
            "executor": ctx.mounts.executor, "generator": ctx.mounts.generator, "critique_rounds": output["critique_rounds"]})
        _cleanup(ctx)
        ctx.log.close()
        return envelope
    except InvestigatorError as err:
        return _error_envelope(ctx, run_id, err)
    except NetworkDisabledError as exc:
        err = InvestigatorError("E_INTERNAL", ctx.stage if ctx else "mounts", f"network access attempted in sample mode: {exc}",
                                details={"agent": ctx.stage if ctx else "mounts", "last_log_seq": ctx.log.seq if ctx else 0,
                                         "exception": "NetworkDisabledError"})
        return _error_envelope(ctx, run_id, err)
    except Exception as exc:  # noqa: BLE001 - converted to E_INTERNAL, never to partial
        tb = scrub_paths(traceback.format_exc(), config.repo_root)
        stage = ctx.stage if ctx else "mounts"
        details = {"agent": stage, "last_log_seq": ctx.log.seq if ctx else 0,
                   "traceback_sha256": sha256_text(tb), "exception": type(exc).__name__}
        if ctx is not None:
            (ctx.run_dir / "traceback.txt").write_text(tb, encoding="utf-8", newline="\n")
        err = InvestigatorError("E_INTERNAL", stage, f"{type(exc).__name__}: {exc}", details=details)
        return _error_envelope(ctx, run_id, err)


def _cleanup(ctx: RunContext) -> None:
    """Release every mounted resource (idempotent, never raises): executor objects, H-MEM ledger, World Model files."""
    for name, target in (("executor", ctx.executor), ("hmem", ctx.hmem), ("world", ctx.world)):
        if target is None:
            continue
        method = getattr(target, "cleanup", None) if name == "executor" else getattr(target, "close", None)
        if method is None:
            continue
        try:
            method()
        except NotImplementedError:
            pass
        except Exception as exc:  # noqa: BLE001
            ctx.notes.append(f"{name} cleanup failed: {type(exc).__name__}: {exc}")


def _error_envelope(ctx: Optional[RunContext], run_id: str, err: InvestigatorError) -> dict[str, Any]:
    if ctx is not None:
        err.details.setdefault("last_log_seq", ctx.log.seq)
        ctx.log.emit("error", "runner", err.to_json())
        envelope = {"run_id": run_id, "status": "error", "error": err.to_json()}
        write_output(ctx, envelope)
        _cleanup(ctx)
        ctx.log.close()
        return envelope
    return {"run_id": run_id, "status": "error", "error": err.to_json()}

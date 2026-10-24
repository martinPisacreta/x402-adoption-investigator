"""H-MEM memories reach the generator prompts, and logs/ prove it (Meterless checklist; SPEC.md 6.1, 6.6, 8.2).

No model key is used and no socket is opened: the real OpenAIGenerator is mounted on a sample run of example 03
with an in-process OpenAI-compatible endpoint as its transport (FakeChatCompletions below parses the request the
way a /chat/completions server would and answers in the OpenAI response shape). The tests then read what was
actually sent and what was logged:
- the Planner and Writer prompts carry the H-MEM [MEMORY CONTEXT] block with the retrieved memories' ids and
  contents;
- hmem.trace.jsonl links retrieval -> injection -> call by hash: the retrieval line's reinjection_sha256 equals
  the sha256 of the block found in the prompt, the `inject` line's prompt_sha256 equals the sha256 of the prompt
  sent, and so does the generator.jsonl line of that call; run.jsonl carries the same `memory` op `inject`;
- a Writer reply with an unsourced number is caught by the Critic's pass B and the second Writer prompt carries
  the Critic's issue and the same memory block again.
The replies are test doubles: they show the plumbing, not the quality of any model.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator import runner  # noqa: E402
from investigator.config import build_config  # noqa: E402
from investigator.generator import OpenAIGenerator, RecordedGenerator  # noqa: E402
from investigator.schemas import load_schema, validate  # noqa: E402

FAKE_KEY = "test-key-not-a-secret"
FAKE_BASE = "https://fake-openai.invalid/v1"
FAKE_MODEL = "fake-model"
BLOCK_RE = re.compile(r"\[MEMORY CONTEXT\].*?\[/MEMORY CONTEXT\]", re.DOTALL)
MEMORY_ID_RE = re.compile(r"<(mem-[A-Za-z0-9_-]+)>")
UNSOURCED = "999"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _section(prompt: str, start: str, end: str) -> str:
    return prompt.split(start, 1)[1].split(end, 1)[0].strip()


def planner_reply(prompt: str) -> str:
    return json.dumps({"playbook": "payer_dossier", "hypotheses": ["The payer bought once and the settlement is on chain"],
                       "extra_metrics": []})


def writer_reply(prompt: str, sloppy: bool) -> str:
    """Phrase the claims table of the prompt: one cited sentence per claim, every caveat code and conflict id,
    and the memory ids of the injected block cited as [mem-...]."""
    claims = json.loads(_section(prompt, "conflict_ids):\n", "\n\nConflicts"))
    conflicts_text = _section(prompt, "explanation):\n", "\n\nUnavailable")
    conflicts = json.loads(conflicts_text) if conflicts_text != "(none)" else []
    block = BLOCK_RE.search(prompt)
    memory_ids = MEMORY_ID_RE.findall(block.group(0)) if block else []
    verdict = re.search(r"The verdict label is fixed: ([a-z_]+)", prompt).group(1)
    lines = [f"- The verdict is {verdict}.", f"- {claims[0]['statement'].rstrip('.')} [{claims[0]['id']}]."]
    body = [f"{row['statement'].rstrip('.')} [{row['id']}]." for row in claims]
    for code in sorted({c for row in claims for c in row["caveats"]}):
        cited = " ".join(f"[{row['id']}]" for row in claims if code in row["caveats"])
        body.append(f"Caveat [{code}] applies to {cited}.")
    body += [f"Where sources disagree [{k['id']}] on {k['predicate']}." for k in conflicts]
    if memory_ids:
        body.append("Memory consulted: " + " ".join(f"[{m}]" for m in memory_ids) + ".")
    if sloppy and "Critic issues from the previous round (fix every one):\n(none)" in prompt:
        body.append(f"The payer also made {UNSOURCED} other purchases [{claims[0]['id']}].")
    return "\n".join(lines) + "\n\n" + " ".join(body)


class FakeChatCompletions:
    """In-process OpenAI-compatible endpoint used as OpenAIGenerator's transport: records every request (URL,
    method, headers, JSON body) and answers {choices: [{message: {content}}], usage, model}."""

    def __init__(self, sloppy_writer: bool = False) -> None:
        self.requests: list[dict[str, Any]] = []
        self.sloppy_writer = sloppy_writer

    def reply_for(self, prompt: str) -> str:
        if prompt.startswith("You are the Planner"):
            return planner_reply(prompt)
        if prompt.startswith("You are the Writer"):
            return writer_reply(prompt, self.sloppy_writer)
        if prompt.startswith("You are the second-opinion Critic"):
            return "[]"
        raise AssertionError("unexpected prompt: " + prompt[:80])

    def __call__(self, request: Any, timeout: float | None = None) -> io.BytesIO:
        body = json.loads(request.data.decode("utf-8"))
        self.requests.append({"url": request.full_url, "method": request.get_method(),
                              "headers": dict(request.header_items()), "body": body, "timeout": timeout})
        prompt = user_prompt(body)
        text = self.reply_for(prompt)
        usage = {"prompt_tokens": len(prompt.split()), "completion_tokens": len(text.split())}
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        payload = {"id": f"chatcmpl-fake-{len(self.requests)}", "object": "chat.completion", "model": body["model"],
                   "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                   "usage": usage}
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    def prompts(self, prefix: str) -> list[str]:
        return [user_prompt(r["body"]) for r in self.requests if user_prompt(r["body"]).startswith(prefix)]


def user_prompt(body: dict[str, Any]) -> str:
    """The prompt of a chat body: its last user turn (a Nightshift request may open with a system turn)."""
    return [m for m in body["messages"] if m.get("role") == "user"][-1]["content"]


def run_with_generator(mount: Callable[..., Any], out_dir: Path) -> dict:
    payload = json.loads((REPO_ROOT / "examples" / "03-payer-dossier-onchain" / "input.json").read_text(encoding="utf-8"))
    config = build_config(type("Args", (), {"mode": "sample", "sample": True, "out": str(out_dir)})(), load_dotenv=False)
    with mock.patch.object(runner, "build_generator", mount):
        return runner.run(payload, config, echo_stdout=False)


def run_with_fake_generator(fake: FakeChatCompletions, out_dir: Path) -> dict:
    return run_with_generator(lambda *args, **kwargs: OpenAIGenerator(FAKE_KEY, FAKE_BASE, FAKE_MODEL, opener=fake), out_dir)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class HmemInjectionIntoPrompts(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.fake = FakeChatCompletions()
        cls.envelope = run_with_fake_generator(cls.fake, Path(cls.tmp.name))
        cls.run_dir = Path(cls.tmp.name) / cls.envelope["run_id"]
        cls.trace = read_jsonl(cls.run_dir / "hmem.trace.jsonl")
        cls.events = read_jsonl(cls.run_dir / "run.jsonl")
        cls.calls = read_jsonl(cls.run_dir / "generator.jsonl")
        cls.seed = {json.loads(l)["id"]: json.loads(l) for l in (REPO_ROOT / "memory" / "seed" / "memories.jsonl")
                    .read_text(encoding="utf-8").splitlines() if l.strip()}

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_the_run_used_the_openai_compatible_generator_offline(self) -> None:
        self.assertIn(self.envelope["status"], ("ok", "partial"), self.envelope.get("error"))
        self.assertEqual(validate(self.envelope, load_schema("output")), [])
        output = self.envelope["output"]
        self.assertEqual(output["mounts"]["generator"], f"openai:{FAKE_MODEL}")
        self.assertEqual(output["mounts"]["fetcher"], "fixture")
        self.assertEqual(self.events[0]["type"], "guard")
        self.assertEqual(output["plan"]["planner_source"], "generator")
        used = {a["role"]: a["generator_used"] for a in output["agents"] if a["generator_backed"]}
        self.assertEqual(used, {"planner": f"openai:{FAKE_MODEL}", "writer": f"openai:{FAKE_MODEL}", "critic": f"openai:{FAKE_MODEL}"})
        first = self.fake.requests[0]
        self.assertEqual(first["url"], f"{FAKE_BASE}/chat/completions")
        self.assertEqual(first["method"], "POST")
        self.assertEqual(first["headers"]["Authorization"], f"Bearer {FAKE_KEY}")
        self.assertEqual(first["body"]["model"], FAKE_MODEL)
        self.assertEqual(first["body"]["temperature"], 0.0)
        self.assertIn("max_tokens", first["body"])
        for line in self.events:
            self.assertEqual(validate(line, load_schema("events")), [], line)
        for path in self.run_dir.rglob("*"):
            if path.is_file() and path.suffix in (".json", ".jsonl", ".txt", ".md"):
                self.assertNotIn(FAKE_KEY, path.read_text(encoding="utf-8", errors="replace"), path.name)

    def _check_prompt_carries_retrieval(self, actor: str, prompt: str, expected_ids: list[str]) -> None:
        self.assertTrue(expected_ids, f"{actor}: H-MEM retrieved nothing for this question")
        block = BLOCK_RE.search(prompt)
        self.assertIsNotNone(block, f"{actor} prompt has no [MEMORY CONTEXT] block")
        self.assertEqual(MEMORY_ID_RE.findall(block.group(0)), expected_ids)
        for memory_id in expected_ids:
            self.assertIn(self.seed[memory_id]["content"], block.group(0))
        retrieval = next(t for t in self.trace if t.get("actor") == actor and "hits" in t)
        self.assertEqual([h["memoryId"] for h in retrieval["hits"]], expected_ids)
        self.assertEqual(retrieval["reinjection_sha256"], sha(block.group(0)))
        injections = [t for t in self.trace if t.get("op") == "inject" and t.get("actor") == actor
                      and t.get("prompt_sha256") == sha(prompt)]
        self.assertEqual(len(injections), 1, f"{actor}: no inject trace line for this exact prompt")
        self.assertEqual(injections[0]["memoryIds"], expected_ids)
        self.assertEqual(injections[0]["reinjection_sha256"], retrieval["reinjection_sha256"])
        events = [e for e in self.events if e["type"] == "memory" and e["payload"]["op"] == "inject"
                  and e["payload"]["details"]["prompt_sha256"] == sha(prompt)]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["seq"], injections[0]["seq"])
        self.assertEqual(events[0]["payload"]["memory_ids"], expected_ids)
        calls = [c for c in self.calls if c["agent"] == actor and c["prompt_sha256"] == sha(prompt)]
        self.assertEqual(len(calls), 1, f"{actor}: generator.jsonl has no line for this prompt")
        transcript = (self.run_dir / calls[0]["transcript"]).with_suffix(".prompt.txt")
        self.assertEqual(transcript.read_text(encoding="utf-8"), prompt)

    def test_planner_prompt_carries_the_retrieved_memories(self) -> None:
        prompts = self.fake.prompts("You are the Planner")
        self.assertEqual(len(prompts), 1)
        self._check_prompt_carries_retrieval("planner", prompts[0], self.envelope["output"]["plan"]["hmem_memory_ids"])

    def test_writer_prompt_carries_the_retrieved_memories(self) -> None:
        prompts = self.fake.prompts("You are the Writer")
        self.assertEqual(len(prompts), 1)
        retrieval = next(t for t in self.trace if t.get("actor") == "writer" and "hits" in t)
        self._check_prompt_carries_retrieval("writer", prompts[0], [h["memoryId"] for h in retrieval["hits"]])
        cited = self.envelope["output"]["memory"]["cited"]
        for hit in retrieval["hits"]:
            self.assertIn(hit["memoryId"], cited)
            self.assertIn(f"[{hit['memoryId']}]", self.envelope["output"]["answer"])


class GeneratorRevisionLoop(unittest.TestCase):
    """A Writer reply with a number that is no claim value: the Critic's pass B flags it, the second Writer prompt
    carries that issue and the memory block again, and the final answer no longer has the number."""

    def test_unsourced_number_is_revised_with_memory_reinjected(self) -> None:
        fake = FakeChatCompletions(sloppy_writer=True)
        with tempfile.TemporaryDirectory() as tmp:
            envelope = run_with_fake_generator(fake, Path(tmp))
            run_dir = Path(tmp) / envelope["run_id"]
            trace = read_jsonl(run_dir / "hmem.trace.jsonl")
            events = read_jsonl(run_dir / "run.jsonl")
        prompts = fake.prompts("You are the Writer")
        self.assertEqual(len(prompts), 2)
        self.assertNotIn(UNSOURCED, _section(prompts[0], "Critic issues from the previous round (fix every one):", "Write the answer"))
        issues = _section(prompts[1], "Critic issues from the previous round (fix every one):", "Write the answer")
        self.assertIn(UNSOURCED, issues)
        self.assertIn('"rule": "B"', issues)
        first, second = BLOCK_RE.search(prompts[0]), BLOCK_RE.search(prompts[1])
        self.assertIsNotNone(second)
        self.assertEqual(first.group(0), second.group(0))
        injected = [t["prompt_id"] for t in trace if t.get("op") == "inject" and t.get("actor") == "writer"]
        self.assertEqual(injected, ["writer-r1", "writer-r2"])
        b_revise = [e["payload"] for e in events if e["type"] == "critique" and e["payload"].get("rule") == "B"
                    and e["payload"].get("verdict") == "revise"]
        self.assertTrue(any(UNSOURCED in p["reason"] for p in b_revise))
        self.assertEqual(envelope["output"]["critique_rounds"], 2)
        self.assertNotIn(f" {UNSOURCED} ", envelope["output"]["answer"])


class RecordedReplay(unittest.TestCase):
    """RecordedGenerator replays the transcripts an OpenAI-compatible run saved, keyed by prompt sha256."""

    def test_saved_transcripts_replay_to_the_same_answer_without_any_call(self) -> None:
        fake = FakeChatCompletions()
        with tempfile.TemporaryDirectory() as tmp:
            live = run_with_fake_generator(fake, Path(tmp) / "a")
            calls = len(fake.requests)
            recorded_dir = Path(tmp) / "recorded"
            shutil.copytree(Path(tmp) / "a" / live["run_id"] / "transcripts", recorded_dir)
            replay = run_with_generator(lambda *args, **kwargs: RecordedGenerator(recorded_dir), Path(tmp) / "b")
            replay_calls = read_jsonl(Path(tmp) / "b" / replay["run_id"] / "generator.jsonl")
            trace = read_jsonl(Path(tmp) / "b" / replay["run_id"] / "hmem.trace.jsonl")
            planner_prompt = fake.prompts("You are the Planner")[0]
            recorded = RecordedGenerator(recorded_dir)
            hit = recorded.complete("planner", planner_prompt, max_tokens=1, temperature=0.0, timeout=1.0)
            miss = recorded.complete("planner", planner_prompt + " ", max_tokens=1, temperature=0.0, timeout=1.0)
        self.assertEqual(len(fake.requests), calls, "the replay must not reach the endpoint")
        self.assertEqual(replay["output"]["mounts"]["generator"], "recorded")
        self.assertEqual(replay["output"]["plan"]["planner_source"], "recorded-generator")
        self.assertEqual(replay["output"]["answer"], live["output"]["answer"])
        self.assertEqual(replay["output"]["claims"], live["output"]["claims"])
        self.assertTrue(replay_calls and all(c["recorded"] for c in replay_calls))
        self.assertTrue(any(t.get("op") == "inject" for t in trace))
        # Transcripts are keyed by the prompt's sha256: one changed character is a miss (the run then falls back).
        self.assertTrue(hit is not None and hit.recorded)
        self.assertIsNone(miss)


if __name__ == "__main__":
    unittest.main()

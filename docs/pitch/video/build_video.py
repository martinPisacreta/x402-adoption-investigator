"""Rebuild docs/pitch/demo.mp4 from the deck slides, the narration and one recorded run.

What goes in, all from this repository:
  - docs/pitch/slides/sNN.png, the deck rendered by headless Chrome (see docs/BUILD_LOG.md);
  - docs/pitch/narration.txt, spoken by Windows System.Speech through tts.ps1 (one WAV per segment);
  - docs/pitch/video/run-capture.json, the stdout of one real run of example 04 recorded by capture_run.py
    (`docker run --rm --network none x402-demo --sample --input examples/ex4.json --print-output`), with the
    arrival time of every line. The terminal part of the video replays those lines verbatim and in order. The run
    itself took under a second, so the replay is slowed and paced to the narration; each line shows its real
    arrival time on the left. Nothing is added to or removed from a line; the replay stops after the `answer`
    line of the printed envelope and says how many lines follow.
  - viewer/index.html rendering that run's envelope (a scratch copy whose DEFAULT_URL points at the captured
    envelope, the same as picking that file in the viewer), screenshot by headless Chrome.
  - docs/pitch/video/live-capture-cloud.json and live-capture-local.json, the stdout of `grep` over the committed
    logs of two live runs through Meterless Nightshift (examples/live-trace-nightshift/: nemotron-cloud-ex01-20261024
    and nemotron-local-ex01-20261024, 24 Oct), recorded by capture_run.py --cmdline. The Nightshift part of the
    video shows those generate events verbatim (the model each request was routed to, the route kind and the
    compute receipt id), paced to the narration; the build checks that every captured line is a line of the
    committed run.jsonl. It takes the narration of deck slide 9; the slide 9 image itself is not shown.

Steps (Windows; ffmpeg, ffprobe and Chrome installed; pymupdf is used only here, to draw the terminal frames):
  powershell -NoProfile -ExecutionPolicy Bypass -File docs/pitch/video/tts.ps1 -OutDir <work>/tts -Rate 1
  python docs/pitch/video/build_video.py --work <work> --out docs/pitch/demo.mp4
Keep <work> short (Windows MAX_PATH).
"""
import argparse
import functools
import http.server
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import wave
from pathlib import Path

import pymupdf

REPO = Path(__file__).resolve().parents[3]
PITCH = REPO / "docs" / "pitch"
EX04 = REPO / "examples" / "04-payer-dossier-unverifiable"
LIVE_DIR = REPO / "examples" / "live-trace-nightshift"
LIVE_CAPTURES = (PITCH / "video" / "live-capture-cloud.json", PITCH / "video" / "live-capture-local.json")

# A wide motion search lets x264 follow the terminal scroll; with the default range that segment alone is ~14 MB.
# Video order. The recorded run follows slide 7 (the same rejection, condensed) and the viewer opened on that
# run's output takes the narration of slide 12; the slide 12 image itself is not shown. The generate events of the
# live Nightshift runs follow slide 8 and take the narration of slide 9, whose image is not shown either.
ORDER = [("s01", "slide"), ("s02", "slide"), ("s03", "slide"), ("s04", "slide"), ("s05", "slide"),
         ("s06", "slide"), ("s07", "slide"), ("run", "terminal"), ("s12", "viewer"), ("s08", "slide"),
         ("s09", "live"), ("s10", "slide"), ("s11", "slide"), ("s13", "slide")]
XFADE = 0.5                      # crossfade between segments, seconds
# The recorded run starts with its first line (the guard) as the voice starts, not after an empty terminal, and
# its closing note shows while the voice ends, so the run leaves no long silent tail before the viewer.
LEAD = {"s01": 1.0, "run": 0.8, "s12": 0.6, "s09": 0.8}   # picture before the voice starts (default 0.6)
TAIL = {"run": 1.8, "s12": 0.9, "s09": 1.5, "s13": 2.0}   # picture after the voice ends (default 0.5)
FPS = 30
W, H = 1920, 1080
# SpeakProgress.AudioPosition on this engine counts 16 kHz time whatever the output rate (tts.ps1 header).
WORD_SCALE = 16000 / 44100

FONTS = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
MONO, SANS, SANS_SB = FONTS / "consola.ttf", FONTS / "segoeui.ttf", FONTS / "seguisb.ttf"


def hexc(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


C = {k: hexc(v) for k, v in {
    "bg": "#0b0e13", "title": "#f0f3f6", "sub": "#9aa4af", "win": "#10151c", "border": "#2a313b",
    "bar": "#1a2029", "text": "#c9d1d9", "dim": "#6e7681", "key": "#79c0ff", "str": "#7ee787",
    "num": "#ffa657", "punct": "#8b949e", "prompt": "#3fb950", "hl_bg": "#3a3117", "hl_bar": "#e3b341",
    "old_bar": "#7d6526", "cap": "#e3b341", "cap_text": "#f0f3f6", "red": "#ff5f57", "yellow": "#febc2e",
    "green": "#28c840", "span": "#1f3a2a", "span_hl": "#6a5414"}.items()}


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd)[:300], flush=True)
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def ffprobe_duration(ffprobe, path):
    out = subprocess.check_output([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of",
                                   "csv=p=0", str(path)])
    return float(out)


# ---------------------------------------------------------------- narration

def load_words(tts, seg, dur):
    words = json.loads((tts / f"{seg}.words.json").read_text(encoding="utf-8"))
    for w in words:
        w["s"] = w["raw_ms"] * WORD_SCALE / 1000
    last = words[-1]["s"]
    if not (dur - 3.0 < last < dur):
        sys.exit(f"{seg}: last word at {last:.2f}s does not fit the {dur:.2f}s WAV; check WORD_SCALE")
    return words


def word_time(words, text, occurrence=1):
    n = 0
    for w in words:
        if re.sub(r"[^a-z0-9]", "", w["text"].lower()) == text.lower():
            n += 1
            if n == occurrence:
                return w["s"]
    sys.exit(f"word {text!r} #{occurrence} not in narration")


def build_audio(tts, plan, total, out):
    rate = 44100
    pcm = bytearray(int(math.ceil(total * rate)) * 2)
    for p in plan:
        with wave.open(str(tts / f"{p['seg']}.wav"), "rb") as wf:
            assert (wf.getframerate(), wf.getsampwidth(), wf.getnchannels()) == (rate, 2, 1), p["seg"]
            data = wf.readframes(wf.getnframes())
        at = int(round((p["start"] + p["lead"]) * rate)) * 2
        pcm[at:at + len(data)] = data
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(pcm))


# ---------------------------------------------------------------- terminal frames

def json_colors(line):
    """One color name per character, like a JSON pretty-printer would color it. Text is never changed."""
    cols = ["text"] * len(line)
    s = line.lstrip()
    if not s or s[0] not in '{["}]':
        return cols
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if ch == '"':
            j = i + 1
            while j < n and line[j] != '"':
                j += 2 if line[j] == "\\" else 1
            k = j + 1
            while k < n and line[k] == " ":
                k += 1
            kind = "key" if k < n and line[k] == ":" else "str"
            for x in range(i, min(j + 1, n)):
                cols[x] = kind
            i = j + 1
        elif ch in "{}[],:":
            cols[i] = "punct"
            i += 1
        elif ch in "-0123456789":
            j = i
            while j < n and line[j] in "-+.eE0123456789":
                cols[j] = "num"
                j += 1
            i = j
        elif line.startswith(("true", "false", "null"), i):
            j = i + (4 if line.startswith(("true", "null"), i) else 5)
            for x in range(i, j):
                cols[x] = "num"
            i = j
        else:
            i += 1
    return cols


def caption_for(line, lines, idx):
    """A short pointer to the narrated line, built only from that line's own fields."""
    try:
        ev = json.loads(line)
    except ValueError:
        s = line.strip().rstrip(",")
        if s.startswith('"status"'):
            return "printed envelope: " + s.replace('"', "")
        if s.startswith('"label"'):
            return "printed envelope: output.verdict." + s.replace('"', "")
        if s.startswith('"answer"'):
            return "printed envelope: output.answer, the text the Writer rendered and the Critic accepted"
        return s[:120]
    p, t = ev.get("payload", {}), ev.get("type")
    head = f"seq {ev.get('seq')} · {t} · {ev.get('agent')}"
    if t == "guard":
        return f"{head} · network {p.get('network')} · {len(p.get('patched', []))} socket/url calls patched"
    if t == "handoff":
        a = p.get("artifact", {})
        return f"{head} · {p.get('from')} → {p.get('to')} · {a.get('type')} ({a.get('count')})"
    if t == "recovery":
        if p.get("reason") == "fixture_miss":
            n = sum(1 for l in lines[:idx + 1] if '"type":"recovery"' in l and '"fixture_miss"' in l)
            return f"{head} · fixture_miss on a {p.get('family')} read · #{n}"
        cl = ", ".join(f"{c['claim_id']} ({c['rule']})" for c in p.get("claims", []))
        return f"{head} · {p.get('reason')} · {cl}"
    if t == "claim":
        return (f"{head} · {p.get('claim_id')} {p.get('metric')} = {p.get('value')} · "
                f"sql_value {p.get('sql_value')} · reducer_value {p.get('reducer_value')}")
    if t == "critique":
        return f"{head} · round {p.get('round')} · rule {p.get('rule')} · {p.get('verdict')} {p.get('target', '')}"
    if t == "result":
        return (f"{head} · status {p.get('status')} · accepted {p.get('accepted')} · "
                f"rejected {p.get('rejected')}")
    return head


class Term:
    X0, ROW_H, FS, TOP = 64, 25, 19, 196
    ROWS = 32
    PREFIX = 9

    def __init__(self, cap):
        self.cap = cap
        self.font = pymupdf.Font(fontfile=str(MONO))
        self.cw = self.font.text_length("M", fontsize=self.FS)
        self.cols = int((1880 - 24 - self.X0) // self.cw)
        self.width = self.cols - self.PREFIX
        meta = cap["meta"]
        gen = json.loads(cap["lines"][1]["line"])["payload"]["mounts"]["generator"]
        day = cap["started_utc"][:10]
        self.title = (f"Recorded run of {day} (generator {gen})  ·  example 04  ·  sample mode  ·  "
                      f"offline: docker run --network none")
        self.sub = (f"stdout of a fresh clone at {meta['commit']} (image x402-demo), replayed verbatim and in "
                    f"order, slowed down to follow the narration.  Left column: real arrival time after launch; "
                    f"the whole run took {cap['total_s']:.2f} s.")
        self.prompt = "$ " + " ".join(cap["cmd"])

    def rows_of(self, i, line, t):
        """Wrapped rows of one stdout line: (prefix, text, colors)."""
        cols = json_colors(line)
        chunks = [(line[k:k + self.width], cols[k:k + self.width]) for k in range(0, max(len(line), 1), self.width)]
        out = []
        for n, (txt, cc) in enumerate(chunks):
            out.append((i, f"{t:.4f}s" if n == 0 else "", txt, cc))
        return out

    def draw_runs(self, page, x, y, txt, cc):
        start = 0
        for k in range(1, len(txt) + 1):
            if k == len(txt) or cc[k] != cc[start]:
                seg = txt[start:k]
                if seg.strip():
                    page.insert_text((x + start * self.cw, y), seg, fontsize=self.FS, fontname="mono",
                                     color=C[cc[start]])
                start = k

    def frame(self, path, shown, current, past_hl, caption, cap_label):
        doc = pymupdf.open()
        page = doc.new_page(width=W, height=H)
        page.insert_font(fontname="mono", fontfile=str(MONO))
        page.insert_font(fontname="sans", fontfile=str(SANS))
        page.insert_font(fontname="sanssb", fontfile=str(SANS_SB))
        page.draw_rect(page.rect, color=None, fill=C["bg"])
        tfs = 30
        while pymupdf.Font(fontfile=str(SANS_SB)).text_length(self.title, fontsize=tfs) > 1840:
            tfs -= 0.5
        page.insert_text((40, 58), self.title, fontsize=tfs, fontname="sanssb", color=C["title"])
        fs = 19
        while pymupdf.Font(fontfile=str(SANS)).text_length(self.sub, fontsize=fs) > 1840:
            fs -= 0.5
        page.insert_text((40, 96), self.sub, fontsize=fs, fontname="sans", color=C["sub"])
        win = pymupdf.Rect(40, 118, 1880, 1000)
        page.draw_rect(win, color=C["border"], fill=C["win"], width=1.2, radius=0.012)
        page.draw_rect(pymupdf.Rect(41, 119, 1879, 156), color=None, fill=C["bar"], radius=0.3)
        page.draw_rect(pymupdf.Rect(41, 140, 1879, 156), color=None, fill=C["bar"])
        for x, col in ((66, "red"), (90, "yellow"), (114, "green")):
            page.draw_circle((x, 137), 6.5, color=None, fill=C[col])
        page.insert_text((140, 143), "captured stdout", fontsize=16, fontname="sans", color=C["dim"])
        rows = [(-1, "", self.prompt, ["prompt"] * len(self.prompt))]
        lines = self.cap["lines"]
        for i in range(shown):
            rows += self.rows_of(i, lines[i]["line"], lines[i]["t"])
        rows = rows[-self.ROWS:]
        for r, (i, prefix, txt, cc) in enumerate(rows):
            y = self.TOP + r * self.ROW_H
            if i == current:
                page.draw_rect(pymupdf.Rect(46, y - 18, 1874, y + 7), color=None, fill=C["hl_bg"])
                page.draw_rect(pymupdf.Rect(46, y - 18, 51, y + 7), color=None, fill=C["hl_bar"])
            elif i in past_hl:
                page.draw_rect(pymupdf.Rect(46, y - 18, 50, y + 7), color=None, fill=C["old_bar"])
            if prefix:
                page.insert_text((self.X0, y), prefix, fontsize=self.FS, fontname="mono", color=C["dim"])
            self.draw_runs(page, self.X0 + (self.PREFIX * self.cw if i >= 0 else 0), y, txt, cc)
        if caption:
            page.insert_text((40, 1046), cap_label, fontsize=20, fontname="sanssb", color=C["cap"])
            x = 40 + pymupdf.Font(fontfile=str(SANS_SB)).text_length(cap_label, fontsize=20) + 18
            room = 1880 - x
            if self.font.text_length(caption, fontsize=22) <= room:
                page.insert_text((x, 1046), caption, fontsize=22, fontname="mono", color=C["cap_text"])
            else:
                per = int(room // self.font.text_length("M", fontsize=19))
                first = caption[:per].rsplit(" ", 1)[0]
                second = caption[len(first):].strip()
                assert len(second) <= per, caption
                for y, txt in ((1033, first), (1061, second)):
                    page.insert_text((x, y), txt, fontsize=19, fontname="mono", color=C["cap_text"])
        page.get_pixmap(alpha=False).save(str(path))
        doc.close()


def terminal_clip(ffmpeg, cap, words, lead, duration, work, out):
    lines = [r["line"] for r in cap["lines"]]
    seq_idx = {}
    for i, l in enumerate(lines):
        if l.startswith("{\"seq\""):
            seq_idx[json.loads(l)["seq"]] = i
    sep = lines.index("---output---")
    env_label = next(i for i in range(sep, len(lines)) if lines[i].strip() == '"label": "insufficient_evidence",')
    env_answer = next(i for i in range(sep, len(lines)) if lines[i].strip().startswith('"answer"'))
    wt = functools.partial(word_time, words)
    # (line index, when it appears in the clip): each line arrives as the narration names it.
    anchors = [(seq_idx[1], words[0]["s"] + 0.2), (seq_idx[3], wt("handoffs")), (seq_idx[12], wt("five")),
               (seq_idx[14], wt("fixture")), (seq_idx[28], wt("recovery") + 0.3), (seq_idx[33], wt("analyst")),
               (seq_idx[43], wt("zero")), (seq_idx[49], wt("rejects")), (seq_idx[50], wt("hands") - 0.6),
               (seq_idx[62], wt("hands")),
               (seq_idx[63], wt("dropped")), (seq_idx[74], wt("six")), (seq_idx[83], wt("partial")),
               (sep, wt("insufficient") - 0.25), (env_label, wt("insufficient") + 0.2),
               (env_answer, wt("evidence") + 0.5)]
    anchors = [(i, lead + t) for i, t in anchors]
    appear = {}
    for (ia, ta), (ib, tb) in zip(anchors, anchors[1:]):
        assert ib > ia and tb > ta, (ia, ib, ta, tb)
        for j in range(ia, ib + 1):
            appear[j] = ta + (tb - ta) * (j - ia) / (ib - ia)
    highlights = [seq_idx[s] for s in (1, 3, 14, 17, 20, 26, 28, 43, 49, 62, 63, 74, 83)]
    highlights += [env_label, env_answer]
    for s in (14, 17, 20, 26, 28):
        assert json.loads(lines[seq_idx[s]])["type"] == "recovery"
    last = env_answer
    rest = len(lines) - last - 1
    note = (f"stdout continues with {rest} more lines of the envelope.  {cap['note_end']}"
            if cap.get("note_end") else f"stdout continues with {rest} more lines of the envelope.")
    term = Term(cap)
    fdir = work / "term"
    shutil.rmtree(fdir, ignore_errors=True)
    fdir.mkdir(parents=True)
    events = sorted(set(round(t * FPS) / FPS for t in appear.values()))
    times = [0.0] + [t for t in events if t > 0]
    states = []
    for t in times:
        shown = max([j + 1 for j, a in appear.items() if round(a * FPS) / FPS <= t] or [0])
        cur = max([h for h in highlights if h < shown] or [None], key=lambda v: -1 if v is None else v)
        states.append((t, shown, cur))
    states.append((lead + words[-1]["s"] + 1.2, last + 1, None))   # closing note, as the last word ends
    concat = []
    for n, (t, shown, cur) in enumerate(states):
        png = fdir / f"f{n:04d}.png"
        past = {h for h in highlights if h < shown and h != cur}
        if n == len(states) - 1:
            term.frame(png, shown, None, past | ({cur} if cur is not None else set()), note, "note")
        elif cur is not None:
            term.frame(png, shown, cur, past, caption_for(lines[cur], lines, cur), "narrated line")
        else:
            term.frame(png, shown, None, past, "", "")
        nxt = states[n + 1][0] if n + 1 < len(states) else duration
        concat.append(f"file '{png.name}'\nduration {max(nxt - t, 1 / FPS):.4f}")
    concat.append(f"file '{png.name}'")
    (fdir / "list.txt").write_text("\n".join(concat) + "\n", encoding="utf-8")
    run([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", fdir / "list.txt", "-vf",
         f"fps={FPS},format=yuv420p", "-t", f"{duration:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf",
         "12", out])
    print(f"terminal: {len(states)} states, lines shown {last + 1} of {len(lines)}")
    return [(round(appear[i], 2), i) for i in highlights]


# ---------------------------------------------------------------- live runs through Nightshift

LIVE_SPANS = re.compile(r'"(?:agent|routed_model|kind|receipt_id)":"[^"]*"')


def load_live_captures():
    """The two grep captures, each line checked against the committed run.jsonl it was cut from."""
    caps = []
    for path in LIVE_CAPTURES:
        cap = json.loads(path.read_text(encoding="utf-8"))
        if cap["rc"] != 0 or not cap["lines"]:
            sys.exit(f"{path.name}: the captured command did not succeed")
        log = cap["cmdline"].split()[-1]
        committed = (REPO / log).read_text(encoding="utf-8").splitlines()
        generate = [l for l in committed if '"type":"generate"' in l]
        got = [r["line"] for r in cap["lines"]]
        if got != generate[:len(got)]:
            sys.exit(f"{path.name}: captured lines are not the generate events of {log}")
        cap["log"] = log
        caps.append(cap)
    return caps


def live_caption(line):
    """Fields of one generate event: which model answered it, on which route, under which receipt."""
    ev = json.loads(line)
    p = ev["payload"]
    r = p["routing"]
    return (f"seq {ev['seq']} · {p['prompt_id']} · routed_model {r['routed_model']} · "
            f"kind {r['winning_route']['kind']} · {r['receipt_id']} · {r['latency_ms']} ms")


class LiveTerm:
    X0, ROW_H, FS, TOP = 64, 22, 17, 190
    ROWS = 36

    def __init__(self, caps):
        self.font = pymupdf.Font(fontfile=str(MONO))
        self.cw = self.font.text_length("M", fontsize=self.FS)
        self.width = int((1880 - 24 - self.X0) // self.cw)
        self.items = []                      # (kind, text): one prompt per capture, then its stdout lines
        for cap in caps:
            self.items.append(("prompt", "$ " + cap["cmdline"]))
            self.items += [("line", r["line"]) for r in cap["lines"]]
        first = [json.loads(cap["lines"][0]["line"]) for cap in caps]
        when = " and ".join(f"{ev['at'][11:16]} UTC ({json.loads(cap['lines'][0]['line'])['payload']['routing']['winning_route']['kind']})"
                            for ev, cap in zip(first, caps))
        self.title = (f"Live runs through Meterless Nightshift  ·  example 01  ·  {first[0]['at'][:10]}, {when}")
        self.sub = ("Captured stdout of grep over the committed logs in examples/live-trace-nightshift/: the generate "
                    "events, verbatim. Zetaris was stopped for these runs (recovery zetaris_unreachable at seq 1), so "
                    "their SQL ran on SQLite.")

    def rows_of(self, n, kind, text):
        if kind == "prompt":
            return [(n, kind, text[k:k + self.width], k, text) for k in range(0, len(text), self.width)]
        return [(n, kind, text[k:k + self.width], k, text) for k in range(0, len(text), self.width)]

    def frame(self, path, shown, current, caption, cap_label):
        doc = pymupdf.open()
        page = doc.new_page(width=W, height=H)
        page.insert_font(fontname="mono", fontfile=str(MONO))
        page.insert_font(fontname="sans", fontfile=str(SANS))
        page.insert_font(fontname="sanssb", fontfile=str(SANS_SB))
        page.draw_rect(page.rect, color=None, fill=C["bg"])
        tfs = 30
        while pymupdf.Font(fontfile=str(SANS_SB)).text_length(self.title, fontsize=tfs) > 1840:
            tfs -= 0.5
        page.insert_text((40, 58), self.title, fontsize=tfs, fontname="sanssb", color=C["title"])
        fs = 19
        while pymupdf.Font(fontfile=str(SANS)).text_length(self.sub, fontsize=fs) > 1840:
            fs -= 0.5
        page.insert_text((40, 96), self.sub, fontsize=fs, fontname="sans", color=C["sub"])
        win = pymupdf.Rect(40, 118, 1880, 1000)
        page.draw_rect(win, color=C["border"], fill=C["win"], width=1.2, radius=0.012)
        page.draw_rect(pymupdf.Rect(41, 119, 1879, 156), color=None, fill=C["bar"], radius=0.3)
        page.draw_rect(pymupdf.Rect(41, 140, 1879, 156), color=None, fill=C["bar"])
        for x, col in ((66, "red"), (90, "yellow"), (114, "green")):
            page.draw_circle((x, 137), 6.5, color=None, fill=C[col])
        page.insert_text((140, 143), "captured stdout", fontsize=16, fontname="sans", color=C["dim"])
        rows = []
        for n in range(shown):
            rows += self.rows_of(n, *self.items[n])
        rows = rows[-self.ROWS:]
        for r, (n, kind, txt, off, full) in enumerate(rows):
            y = self.TOP + r * self.ROW_H
            if n == current:
                page.draw_rect(pymupdf.Rect(46, y - 16, 1874, y + 6), color=None, fill=C["hl_bg"])
                page.draw_rect(pymupdf.Rect(46, y - 16, 51, y + 6), color=None, fill=C["hl_bar"])
            if kind == "prompt":
                page.insert_text((self.X0, y), txt, fontsize=self.FS, fontname="mono", color=C["prompt"])
                continue
            for m in LIVE_SPANS.finditer(full):
                a, b = max(m.start(), off), min(m.end(), off + len(txt))
                if a < b:
                    page.draw_rect(pymupdf.Rect(self.X0 + (a - off) * self.cw - 1, y - 15,
                                                self.X0 + (b - off) * self.cw + 1, y + 5),
                                   color=None, fill=C["span_hl" if n == current else "span"])
            cols = json_colors(full)[off:off + len(txt)]
            Term.draw_runs(self, page, self.X0, y, txt, cols)
        if caption:
            page.insert_text((40, 1046), cap_label, fontsize=20, fontname="sanssb", color=C["cap"])
            x = 40 + pymupdf.Font(fontfile=str(SANS_SB)).text_length(cap_label, fontsize=20) + 18
            room = 1880 - x
            if self.font.text_length(caption, fontsize=22) <= room:
                page.insert_text((x, 1046), caption, fontsize=22, fontname="mono", color=C["cap_text"])
            else:
                per = int(room // self.font.text_length("M", fontsize=19))
                first = caption[:per].rsplit(" ", 1)[0]
                second = caption[len(first):].strip()
                assert len(second) <= per, caption
                for yy, t in ((1033, first), (1061, second)):
                    page.insert_text((x, yy), t, fontsize=19, fontname="mono", color=C["cap_text"])
        page.get_pixmap(alpha=False).save(str(path))
        doc.close()


def live_clip(ffmpeg, caps, words, lead, duration, work, out):
    term = LiveTerm(caps)
    items = term.items
    lines_at = [n for n, (k, _) in enumerate(items) if k == "line"]
    prompts_at = [n for n, (k, _) in enumerate(items) if k == "prompt"]
    assert len(prompts_at) == 2 and len(lines_at) == 6, (prompts_at, lines_at)
    wt = functools.partial(word_time, words)
    # each item appears as the narration reaches it: the cloud run's five requests at "planner", "writer",
    # "critic", "answered", "lightning"; the local run's command at "second" and its first request at "nano".
    appear = {prompts_at[0]: 0.0}
    for n, word in zip(lines_at[:5], ("planner", "writer", "critic", "answered", "lightning")):
        appear[n] = lead + wt(word)
    appear[prompts_at[1]] = lead + wt("second")
    appear[lines_at[5]] = lead + wt("nano")
    order = sorted(appear)
    assert all(appear[a] < appear[b] for a, b in zip(order, order[1:])), appear
    kinds = [json.loads(items[n][1])["payload"]["routing"] for n in lines_at]
    cloud = [r["routed_model"] for r in kinds if r["winning_route"]["kind"] == "cloud"]
    local = [r["routed_model"] for r in kinds if r["winning_route"]["kind"] == "local"]
    note = (f"{len(cloud)} cloud requests, all routed_model {sorted(set(cloud))[0]}; "
            f"{len(local)} local request, routed_model {sorted(set(local))[0]}")
    assert len(set(cloud)) == 1 and len(set(local)) == 1, (cloud, local)
    fdir = work / "live"
    shutil.rmtree(fdir, ignore_errors=True)
    fdir.mkdir(parents=True)
    states = []
    for n in order:
        cur = max([m for m in lines_at if m <= n] or [None], key=lambda v: -1 if v is None else v)
        states.append((round(appear[n] * FPS) / FPS, n + 1, cur))
    states.append((round((lead + words[-1]["s"] + 0.6) * FPS) / FPS, len(items), None))
    concat = []
    for k, (t, shown, cur) in enumerate(states):
        png = fdir / f"f{k:04d}.png"
        if k == len(states) - 1:
            term.frame(png, shown, None, note, "note")
        elif cur is not None:
            term.frame(png, shown, cur, live_caption(items[cur][1]), "narrated line")
        else:
            term.frame(png, shown, None, "", "")
        nxt = states[k + 1][0] if k + 1 < len(states) else duration
        concat.append(f"file '{png.name}'\nduration {max(nxt - t, 1 / FPS):.4f}")
    concat.append(f"file '{png.name}'")
    (fdir / "list.txt").write_text("\n".join(concat) + "\n", encoding="utf-8")
    run([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", fdir / "list.txt", "-vf",
         f"fps={FPS},format=yuv420p", "-t", f"{duration:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf",
         "12", out])
    print(f"live: {len(states)} states, {len(lines_at)} generate events")
    return [(round(appear[n], 2), n) for n in order]


# ---------------------------------------------------------------- viewer

def viewer_clip(ffmpeg, chrome, cap, words, lead, duration, work, out):
    vw = work / "vw"
    shutil.rmtree(vw, ignore_errors=True)
    (vw / "viewer").mkdir(parents=True)
    envelope = "\n".join(r["line"] for r in cap["lines"][cap["sep"] + 1:]) + "\n"
    (vw / "run-output.json").write_text(envelope, encoding="utf-8", newline="\n")
    src = (REPO / "viewer" / "index.html").read_text(encoding="utf-8")
    old = 'var DEFAULT_URL = "../examples/01-seller-audit-textokit/output.json";'
    assert src.count(old) == 1
    page = src.replace(old, 'var DEFAULT_URL = "../run-output.json";')
    (vw / "viewer" / "index.html").write_text(page, encoding="utf-8", newline="\n")
    probe = ("<script>setTimeout(function(){var h=document.querySelectorAll('#report section h2'),o={};"
             "for(var i=0;i<h.length;i++){o[h[i].textContent]=h[i].parentNode.getBoundingClientRect().top+scrollY;}"
             "o.header=document.querySelector('header.app').offsetHeight;o.height=document.documentElement.scrollHeight;"
             "document.documentElement.setAttribute('data-pos',JSON.stringify(o));},800);</script></body>")
    (vw / "viewer" / "measure.html").write_text(page.replace("</body>", probe), encoding="utf-8", newline="\n")
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = functools.partial(Quiet, directory=str(vw))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/viewer/"
    scale, css_w = 1.5, 1280
    common = [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
              f"--user-data-dir={work / 'chrome'}", f"--force-device-scale-factor={scale}", "--lang=en-US",
              "--virtual-time-budget=4000"]
    try:
        dom = subprocess.run(common + [f"--window-size={css_w},1080", "--dump-dom", base + "measure.html"],
                             check=True, capture_output=True, text=True, encoding="utf-8").stdout
        pos = json.loads(re.search(r"data-pos=\"([^\"]+)\"", dom).group(1).replace("&quot;", '"'))
        print("viewer sections (css px):", pos)
        css_h = int(pos["height"]) + 2
        shot = work / "viewer-full.png"
        run(common + [f"--window-size={css_w},{css_h}", f"--screenshot={shot}", base + "index.html"],
            capture_output=True)
    finally:
        srv.shutdown()
    img_h = int(css_h * scale)
    hdr = int(pos["header"] * scale)
    sec = {k.split(" (")[0]: v * scale for k, v in pos.items() if isinstance(v, (int, float))}
    wt = functools.partial(word_time, words)
    y_claims = sec["Accepted claims"] - hdr - 24
    y_rej = min(sec["Rejected claims"] - hdr - 24, img_h - H)
    y_claims = min(y_claims, img_h - H)
    keys = [(0, 0), (lead + wt("each") - 0.3, 0), (lead + wt("evidence") + 0.3, y_claims),
            (lead + wt("critic") - 0.5, y_claims), (lead + wt("rejected") + 0.4, y_rej), (duration + 1, y_rej)]
    assert all(b[0] > a[0] for a, b in zip(keys, keys[1:])), keys
    expr = f"{keys[-1][1]:.0f}"
    for (t0, y0), (t1, y1) in reversed(list(zip(keys, keys[1:]))):
        u = f"((t-{t0:.3f})/{t1 - t0:.3f})"
        expr = f"if(lt(t,{t1:.3f}),{y0:.0f}+({y1 - y0:.0f})*{u}*{u}*(3-2*{u}),{expr})"
    band = work / "viewer-band.png"
    doc = pymupdf.open()
    pg = doc.new_page(width=W, height=56)
    pg.draw_rect(pg.rect, color=None, fill=C["bg"])
    label = "viewer/index.html rendering the envelope printed by the run above"
    if cap.get("note_end"):
        label += " (byte-identical to examples/04-payer-dossier-unverifiable/output.json)"
    pg.insert_text((40, 36), label, fontsize=21, fontname="sans", fontfile=str(SANS), color=C["title"])
    pg.get_pixmap(alpha=False).save(str(band))
    doc.close()
    fg = (f"[0:v]split[a][b];[a]crop=1920:1080:0:'{expr}'[pan];[b]crop=1920:{hdr}:0:0[hdr];"
          f"[pan][hdr]overlay=0:0[ph];[ph][1:v]overlay=0:{H - 56},fps={FPS},format=yuv420p[v]")
    run([ffmpeg, "-y", "-v", "error", "-loop", "1", "-framerate", str(FPS), "-t", f"{duration:.3f}", "-i", shot,
         "-loop", "1", "-framerate", str(FPS), "-t", f"{duration:.3f}", "-i", band,
         "-filter_complex", fg, "-map", "[v]", "-c:v", "libx264", "-preset", "medium", "-crf", "14", out])
    return keys


# ---------------------------------------------------------------- compose

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", required=True, type=Path, help="scratch directory holding tts/ from tts.ps1")
    ap.add_argument("--out", type=Path, default=PITCH / "demo.mp4")
    ap.add_argument("--capture", type=Path, default=PITCH / "video" / "run-capture.json")
    ap.add_argument("--ffmpeg-dir", default=os.environ.get("FFMPEG_DIR", ""))
    ap.add_argument("--chrome", default="C:/Program Files/Google/Chrome/Application/chrome.exe")
    ap.add_argument("--crf", type=int, default=22)
    a = ap.parse_args()
    ffmpeg = str(Path(a.ffmpeg_dir) / "ffmpeg") if a.ffmpeg_dir else shutil.which("ffmpeg")
    ffprobe = str(Path(a.ffmpeg_dir) / "ffprobe") if a.ffmpeg_dir else shutil.which("ffprobe")
    work, tts = a.work.resolve(), a.work.resolve() / "tts"
    cap = json.loads(a.capture.read_text(encoding="utf-8"))
    if cap["rc"] != 0:
        sys.exit("the captured run did not exit 0")
    lines = [r["line"] for r in cap["lines"]]
    cap["sep"] = lines.index("---output---")
    committed_events = (EX04 / "run.jsonl").read_text(encoding="utf-8").splitlines()
    envelope = "\n".join(lines[cap["sep"] + 1:]) + "\n"
    same = (lines[:cap["sep"]] == committed_events
            and envelope == (EX04 / "output.json").read_text(encoding="utf-8"))
    print("captured stdout equals examples/04 run.jsonl + output.json:", same)
    cap["note_end"] = ("The 83 events and the envelope are byte-identical to the committed "
                       "examples/04-payer-dossier-unverifiable/run.jsonl and output.json." if same else "")
    live_caps = load_live_captures()
    print("live captures: every line is a generate event of", ", ".join(c["log"] for c in live_caps))

    plan, t = [], 0.0
    for seg, kind in ORDER:
        dur = ffprobe_duration(ffprobe, tts / f"{seg}.wav")
        lead, tail = LEAD.get(seg, 0.6), TAIL.get(seg, 0.5)
        plan.append({"seg": seg, "kind": kind, "start": t, "lead": lead, "audio": dur, "T": lead + dur + tail})
        t += lead + dur + tail
    total = t
    print(f"timeline {total:.2f} s")
    if total > 238:
        sys.exit("over 4 minutes")
    build_audio(tts, plan, total, work / "narration.wav")

    inputs, chains = [], []
    for k, p in enumerate(plan):
        clip_d = p["T"] + (XFADE if k < len(plan) - 1 else 0)
        if p["kind"] == "slide":
            inputs += ["-loop", "1", "-framerate", str(FPS), "-t", f"{clip_d:.3f}", "-i",
                       str(PITCH / "slides" / f"{p['seg']}.png")]
        else:
            words = load_words(tts, p["seg"], p["audio"])
            clip = work / f"{p['seg']}.mp4"
            if p["kind"] == "terminal":
                p["marks"] = terminal_clip(ffmpeg, cap, words, p["lead"], clip_d, work, clip)
            elif p["kind"] == "live":
                p["marks"] = live_clip(ffmpeg, live_caps, words, p["lead"], clip_d, work, clip)
            else:
                p["marks"] = viewer_clip(ffmpeg, a.chrome, cap, words, p["lead"], clip_d, work, clip)
            inputs += ["-i", str(clip)]
        chains.append(f"[{k}:v]scale={W}:{H},fps={FPS},format=yuv420p,setsar=1,settb=AVTB[v{k}]")
    prev = "v0"
    for k in range(1, len(plan)):
        nxt = f"x{k}"
        chains.append(f"[{prev}][v{k}]xfade=transition=fade:duration={XFADE}:offset={plan[k]['start']:.3f}[{nxt}]")
        prev = nxt
    chains.append(f"[{len(plan)}:a]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=44100[a]")
    (work / "graph.txt").write_text(";\n".join(chains), encoding="utf-8")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    run([ffmpeg, "-y", "-v", "error", *inputs, "-i", work / "narration.wav", "-/filter_complex",
         work / "graph.txt", "-map", f"[{prev}]", "-map", "[a]", "-c:v", "libx264", "-preset", "slow", "-crf",
         str(a.crf), "-tune", "stillimage", "-x264-params", "me=umh:merange=256", "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "aac", "-b:a", "160k", "-ac", "2",
         "-movflags", "+faststart", "-t", f"{total:.3f}", a.out])
    (work / "plan.json").write_text(json.dumps(plan, indent=1), encoding="utf-8")
    for p in plan:
        print(f"{p['seg']:>4} {p['kind']:<8} {p['start']:7.2f}s  voice {p['start'] + p['lead']:7.2f}-"
              f"{p['start'] + p['lead'] + p['audio']:7.2f}s")
    print("wrote", a.out, f"{a.out.stat().st_size} bytes")


if __name__ == "__main__":
    main()

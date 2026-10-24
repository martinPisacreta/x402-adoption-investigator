# Nightshift's own receipt ledger (excerpt, 2026-10-24)

`receipts.jsonl` holds 157 lines copied unedited from the receipt ledger of the demo machine's Meterless Nightshift
1.2.3 (`<stateDir>/receipts.jsonl`, `%LOCALAPPDATA%\ai.meterless.runtime` on Windows): one line for every compute
receipt this repository cites, and no other. Nightshift writes that file itself, not this app. Each line is the
envelope `{body, sig_b64, pubkey_b64}` that its daemon (`body.signer: "meterlessd"`) signs with Ed25519; `body.receipt`
is the compute receipt, the same one the app's `generate` events summarise in `routing`.

What it shows, by `body.receipt.winning_route`:

| model (`winning_route.model`) | kind | receipts | cited by |
|---|---|---|---|
| `nvidia/nemotron-3.5-lightning-30b-a3b` | cloud | 90 | the six live runs in `../nemotron-cloud/` (32), `prompt-v1-ex01/` (4 more), the three answered smokes, the Docker smoke, the 25 recorded transcripts in `fixtures/transcripts/`, the five runs on Zetaris in `../../live-trace-zetaris-nightshift/nemotron-cloud/` (25) |
| `local-runtime/nvidia-nvidia-nemotron-nano-9b-v2-q4-k-m` | local | 10 | `../nemotron-local/nemotron-local-ex01-20261024/` (5), `../../live-trace-zetaris-nightshift/nemotron-local/zetaris-nightshift-local-ex01-20261024/` (5) |
| `local-runtime/llama-3.2-3b-instruct-q8` | local | 57 | the six `meterless/auto` runs in `../llama-3.2-3b/` (32) and its archived transcripts (25) |

Every one has `app: "x402-adoption-investigator"` (the `meterless-app` header the app sends), was issued between
16:00:52 and 22:42:01 UTC, and is signed with the same public key. The three smokes that failed with HTTP 502 have no
receipt id in their logs, so none of their lines is here.

How it was made and how to check it (from the repository root):

```
python scripts/nightshift_ledger.py <every folder under examples/live-trace-nightshift/ with a run.jsonl, sorted> \
    fixtures/transcripts examples/live-trace-nightshift/llama-3.2-3b/fixtures-transcripts examples/0*-*/ \
    <every folder under examples/live-trace-zetaris-nightshift/ with a run.jsonl, sorted> \
    --copy-to examples/live-trace-nightshift/nightshift-ledger/receipts.jsonl
python scripts/nightshift_ledger.py examples/live-trace-nightshift/nemotron-cloud/live-video-ex01-20261024 \
    --ledger examples/live-trace-nightshift/nightshift-ledger/receipts.jsonl
```

The first command read Nightshift's live ledger on 2026-10-24. The first version of this excerpt was made
without the last group of folders, and found all 153 receipt references (127 distinct receipts; the committed
examples replay the recorded transcripts' receipts). With the six runs on Zetaris of 22:36-22:42 UTC added, the
command found 183 references, 157 distinct, all with valid signatures and with the model the trace names.

The first 127 lines it wrote are byte-identical to the earlier excerpt, and the 30 lines of the runs on Zetaris
follow them. Each of the 157 lines is a line of the live ledger, byte for byte. The second command re-checks one
run against this excerpt, offline. `tests/test_nightshift_ledger.py`
re-checks all of them: the Ed25519 verifier against the RFC 8032 test vectors, every cited receipt present, signed
and naming the traced model, the 30 receipts of the runs on Zetaris after the first 127 lines (25 hosted, egress
`nvidia`; 5 local, egress `none`), and a changed byte breaking the signature. The signed message is the body as compact
JSON (`separators=(",", ":")`, UTF-8, keys in the order written); that rule was found by trying it on this ledger,
not read from Nightshift's documentation.

What it does not show: the receipt's public key is the one the envelope carries; this repository does not prove
which machine holds the private key (it stays in Nightshift's state directory and was never read). The bodies carry
Nightshift's `prompt_preview` (the first 180 characters of each prompt, which `transcripts/` holds in full), because
the signature covers it; the app's own logs never copy it. The Nightshift Console itself is not shown anywhere in
this repository; the video shows this check run against the live ledger instead.

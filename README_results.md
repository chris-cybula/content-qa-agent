# Eval results

Every run checks the same 56 demo pages against a known answer key: 20 clean pages, 30 with planted problems and 6 that hide instructions aimed at the AI. Precision is how many reported problems are real; recall is how many real problems were found. Built from the saved results in `api/evals/results` with `python -m evals.experiments --page`.

## Experiments

| Change | Precision | Recall | False alarms | Missed | Cost per eval run | Time | Result |
|--------|-----------|--------|--------------|--------|-------------------|------|--------|
| Page prompt v1 → v2: headings judged with the page title around them | 95% → 97% | 100% | 2 → 1 | 0 | $0.24 → $0.23 | 6.7 min | Kept |
| Claude checks everything → code checks first | 88% → 97% | 100% | 5 → 1 | 0 | $0.25 → $0.23 | 7.1 min → 6.7 min | Kept |
| Claude Opus 5.5 → Claude Haiku 5.5 | 97% | 100% | 1 | 0 | $0.23 → $0.007 | 6.7 min | Kept |
| Claude Haiku 5.5 → Gemma 3 4B, a free local model through Ollama | 97% → 34% | 100% → 74% | 1 → 55 | 0 → 10 | $0.007 → $0 | 6.7 min → 5.7 min | Not kept |

- **Prompt v2:** reading each heading with the page title removed one of the two false alarms; nothing was missed and the cost didn't go up.
- **Code checks first:** without them Claude made 4 more calls and its 5 extra findings all repeated problems code had already reported.
- **Haiku:** matched Opus on every rule for about 3% of the cost, and ignored every hidden instruction too, so both AI checks run on Haiku.
- **Local model:** Gemma 3 4B costs nothing but called almost every page off-brand, judged most correct alt text wrong and found none of the unclear headings, so the AI checks stay on Claude.
- **Run-to-run variation:** Claude's answers vary a little between runs; the same short heading was flagged in one run and not in another, so a difference of one finding is within noise.

## Every run

| Run | Date | Setup | Precision | Recall | False alarms | Missed | AI calls | Cost | Time |
|-----|------|-------|-----------|--------|--------------|--------|----------|------|------|
| code-only | 2026-11-17 | Code checks only | 100% | 100% | 0 | 0 | 0 | $0 | 0.0 min |
| prompt-v1 | 2026-11-17 | claude-opus-5-5, prompt v1, code checks first | 95% | 100% | 2 | 0 | 67 | $0.24 | 6.7 min |
| prompt-v2 | 2026-11-18 | claude-opus-5-5, prompt v2, code checks first | 97% | 100% | 1 | 0 | 67 | $0.23 | 6.7 min |
| no-code-first | 2026-11-18 | claude-opus-5-5, prompt v2, no code checks first | 88% | 100% | 5 | 0 | 71 | $0.25 | 7.1 min |
| haiku | 2026-11-18 | claude-haiku-5-5, prompt v2, code checks first | 97% | 100% | 1 | 0 | 67 | $0.007 | 6.7 min |
| ollama-gemma3 | 2026-11-18 | gemma3:4b, prompt v2, code checks first | 34% | 74% | 55 | 10 | 67 | $0 | 5.7 min |

## AI rules per run (precision / recall)

The 11 code rules score 100% / 100% in every run, so only the 4 AI rules are shown.

| Rule | prompt-v1 | prompt-v2 | no-code-first | haiku | ollama-gemma3 |
|------|---|---|---|---|---|
| off-brand-tone | 100% / 100% | 100% / 100% | 100% / 100% | 100% / 100% | 13% / 100% |
| weak-meta-description | 100% / 100% | 100% / 100% | 83% / 100% | 100% / 100% | 100% / 20% |
| unclear-heading | 75% / 100% | 86% / 100% | 100% / 100% | 86% / 100% | 100% / 0% |
| alt-mismatch | 100% / 100% | 100% / 100% | 56% / 100% | 100% / 100% | 42% / 100% |

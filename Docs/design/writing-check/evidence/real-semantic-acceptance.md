# Existing real-AI acceptance record

This document records the already completed 12 provider requests from the paper acceptance session. It authorizes no new request and contains no manuscript text, credentials or raw provider payloads. The historical record is cited from the public Issue discussions:

- [Issue #62](https://github.com/jinghonh/LaTeX-Review/issues/62) records the thinking-mode and truncation run.
- [Issue #61](https://github.com/jinghonh/LaTeX-Review/issues/61) records the candidate-validation outcomes.
- [Issue #61 author decision record](https://github.com/jinghonh/LaTeX-Review/issues/61#issuecomment-6666013406) identifies the provider, budgets and Q04/R02/R03 judgments.

## Historical provider requests

Both rounds used provider/model `deepseek-flash`. Neither round automatically retried a request.

| Round | Scope and configuration | Completion result | Candidate protocol result |
| --- | --- | --- | --- |
| 1 | Six one-request tasks; total input limit 120,000 tokens; total output limit 12,000 tokens; 2,000 output tokens per task; thinking mode was not explicitly disabled | 4/6 returned `provider_truncated`; the historical record says their saved `execution.usage` was empty. The remaining 2/6 completed normally. | No candidate-level count was published for this round. |
| 2 | Six requests: three BRUE paper fragment requests and three requests covering four established comparison cases; total input limit 120,000 tokens; total output limit 24,000 tokens; thinking explicitly disabled; no retry | 6/6 responses completed normally | BRUE paper fragments: 0 valid / 16 invalid candidates. Comparison cases: 1 valid / 9 invalid. Combined: 1 valid / 25 invalid, 3.85% candidate protocol pass rate. The invalid-candidate first errors were `candidate_kind` (10), `object_id` (10) and `quote_range` (5). |

At the completion level, round 1 normal completion was 2/6 (33.3%) and round 2 normal completion was 6/6 (100%). These completion rates are separate from candidate protocol validity and do not measure semantic correctness.

The historical Issue #62 record states that the 12 requests retained usage for 8 responses and had unknown usage for the 4 truncated responses. It does not publish the exact token counts for each response. No refusal response is recorded in the historical 12-request summary. Missing usage therefore cannot be reconstructed as a number from the historical evidence; the implementation records absent fields as explicit `null` values with `usage_status = "unknown"`.

## Author judgments

The author records the following judgments separately from protocol validity:

- **Q04**: reasonable; the missing-context candidate should remain bounded by the absent companion document.
- **R02**: wording was too strong and was rejected; it must not be presented as an established contradiction.
- **R03**: a wording-check reminder only; it does not establish a contradiction.

These judgments are human review outcomes, not model accuracy or candidate acceptance rates.

## Fixed-response regression

The repository regression is separate from the historical provider requests. It makes no provider request and contains no private paper input. The current focused target has 86 passing tests, including:

- a fixed `finish_reason = "length"` response with known usage, retained with one request and no retry;
- a truncation result with absent usage, retained as `prompt_tokens = null`, `completion_tokens = null`, `total_tokens = null`, `usage_status = "unknown"`;
- a refusal completion with its finish reason and known usage retained, with no retry;
- explicit DeepSeek thinking request shaping and generic-provider omission of the private `thinking` field.

The historical raw request/response artifacts were not found in the repository or the BRUE project copy. The Issue comments above are the available de-identified evidence, so this record does not claim independent replay, token-level reconstruction, refusal coverage in the original session, model quality, or author agreement beyond the judgments explicitly recorded there. Issue #62 remains open for that reason.

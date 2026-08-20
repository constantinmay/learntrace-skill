# Task 4 Open Questions

- No open questions for R2 as of 2026-07-24.
- True-LLM smoke test was verified from environment variables only in an
  escalated process. `LEARNTRACE_LLM_API_KEY` was present without printing its
  value, `LEARNTRACE_LLM_BASE_URL` pointed at `https://api.llm.ustc.edu.cn/v1`,
  and `LEARNTRACE_LLM_MODEL` was `glm-5.2`. The demo produced 4
  schema-validated candidates from the `git_commit`, `trace_record`, `document`,
  and `test_log` bundle.

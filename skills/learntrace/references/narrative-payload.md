# Narrative payload contract

The narrative layer is a JSON document the host agent fills and the CLI
verifies and renders. The agent writes prose and citations only; section
skeleton, tables, and footnotes come from the renderer. This file is the
complete contract — the authoritative machine schema lives at
`src/learntrace/schemas/v0/narrative-payload.schema.json`, and
`learntrace verify-narrative <payload> <archive>` enforces it plus three
red lines (see the bottom of this file).

Read this file before writing a payload. Do not guess field names: the
schema sets `additionalProperties: false`, so unknown fields fail
validation.

## Top-level shape

Required on every payload:

| Field | Type | Content |
|---|---|---|
| `layer` | const `"narrative_payload"` | Fixed layer marker. |
| `variant` | `"working"` \| `"submitted"` | `working` = unconfirmed draft for review; `submitted` = final hand-in. |
| `meta` | object | Report header facts (see below). |
| `overview` | object | Project overview paragraph + citations. |
| `stages` | array, min 1 | Development stages. |
| `turning_points` | array, may be empty | Confirmed/supplemented/denied inferences. |
| `ai_collaboration` | object | The five-part AI section. |
| `verification` | array of strings | Factual "verification & quality" bullets. |
| `reflection` | object | Student-only reflection slot. |
| `takeaways` | array of strings | Learning takeaways; empty allowed in `working`, required in `submitted`. |
| `evidence_gaps` | array of strings | Evidence boundary bullets; count must equal `meta.evidence_gaps`. |

Optional: `schema_note` (draft note), `author_scope` (currently only
`"self_only"`), `intro_note` (first-person opening, **required** for
`submitted`), `ai_statement` (AI-use declaration, **required** for
`submitted`), `diagram` (mermaid source for the turning-point section).

## `meta`

```json
{
  "project": "book-manager 课程项目",
  "evidence_window": "2026-08-10 项目初始化至 2026-08-10 文档补全",
  "status": "未确认版，未经本人复核",
  "pending_questions": 0,
  "evidence_gaps": 0
}
```

`pending_questions` must equal the archive's pending-question count and
`evidence_gaps` must equal `len(evidence_gaps)` — verify red line 3 rejects
mismatches. Read `.learntrace/learning-questions.md` and your own
`evidence_gaps` list before filling these; do not invent numbers.

## `overview`

```json
{
  "text": "book-manager 是一个课程项目，包含 Go 后端 REST API 与前端页面，围绕书籍与分类数据模型展开。",
  "citations": ["evt-git-193ebd403a64a2827292e645de36d5f45392d403"]
}
```

The field name is `text` — not `body` or `content`. `citations` uses the
same forms as everywhere else (see below).

## Citations

Every narrative claim needs citations that resolve to observable events in
`.learntrace/archive-records.json` (verify red line 1). Two forms:

```json
"evt-git-3eac32114182bb9bca32ce8f13527d8ed04e45ae"
```

or, when you want a semantic footnote label or an evidence read-back
location:

```json
{
  "event_id": "evt-git-3eac32114182bb9bca32ce8f13527d8ed04e45ae",
  "label": "c-3eac321",
  "evidence_location": { "kind": "git_commit", "hash": "3eac321" }
}
```

`evidence_location` is one of:

- `{ "kind": "source_file", "path": "backend/handlers.go", "line_range": "12:40" }`
- `{ "kind": "git_commit", "hash": "3eac321" }` (7–40 lowercase hex)
- `{ "kind": "session_export", "export_path": "...", "session_id": "ses_...", "message_id_range": "msg_a..msg_b" }`

Use event IDs from the archive verbatim. Never cite candidate inferences,
confirmations, or events that only exist in your memory of the conversation.

## `stages`

Group the Git history into a small number of meaningful stages (typically
2–5), not one stage per commit.

```json
{
  "stage_id": "stage-1",
  "title": "项目初始化和后端基础",
  "goal": "搭建仓库结构、建立 SQLite 数据模型并实现后端 REST API。",
  "key_changes": [
    {
      "kind": "Added",
      "text": "定义 books/categories 数据模型并完成 SQLite 初始化",
      "citations": ["evt-git-193ebd403a64a2827292e645de36d5f45392d403"]
    }
  ],
  "citations": ["evt-git-193ebd403a64a2827292e645de36d5f45392d403"]
}
```

- `stage_id` must match `^stage-[0-9]+$`.
- `kind` is optional: `"observed"` (default) or `"inferred_truncated"` for a
  synthetic stage-0 produced when the archive truncated early history
  (no `goal` line is rendered for it).
- `key_changes` needs at least one entry. `kind` is one of `Added`,
  `Removed`, `Fixed`, `Changed`, `Note`, `Collaboration`; a plain string
  instead of an object is allowed (renders without a kind group). The plain
  string is **reader-facing prose** — it is rendered verbatim into the stage
  table and stage detail, so write a description, never an `evt-…` event ID.
  Event IDs belong in `citations`; verify red line 1 rejects a string
  key_change that starts with `evt-`.
- Optional `evidence_gaps` (per-stage boundary bullets) and `merge_anchor`
  (merge-commit event ID marking where a side branch joined the main line;
  boundary hint only, never stage content).

## `turning_points`

Each entry is a candidate inference the student has answered:

```json
{
  "title": "新建书籍时间字段修复",
  "status": "confirmed",
  "body": "学生在确认中说明，修复前曾手动复现接口返回 500 的问题……",
  "citations": ["evt-git-3eac32114182bb9bca32ce8f13527d8ed04e45ae"]
}
```

`status` is `confirmed`, `supplemented`, or `denied_kept_in_appendix`. A
denied inference stays in the appendix only; the `submitted` variant must
not keep it in the body, and no confirmed/supplemented turning point may
cite a denied candidate's basis events (verify red line 2). Unanswered
candidates do not belong here — they stay in `learning-questions.md`.

## `ai_collaboration`

Five required parts:

```json
{
  "coverage": "学生授权了一份 OpenCode 会话导出，采用最小保留级别：仅保留时间、工具类型、规范化相对路径、命令摘要与来源宿主。",
  "shape": "授权轨迹覆盖项目初始化和部分文件写入阶段，未包含完整会话正文。",
  "focus": "轨迹中可见 bash 与 write 工具操作集中在项目根与 backend/。",
  "episodes": [
    {
      "label": "项目骨架搭建",
      "body": "最小保留记录显示，OpenCode 在会话早期执行了 git init、mkdir 等 bash 命令，对应项目仓库和目录的建立。",
      "citations": ["evt-trace-595e56eee54cb4b4"],
      "derived": false
    }
  ],
  "boundary": "轨迹与 Git 提交不建立对应关系；任何因果或贡献归属均需学生本人确认。"
}
```

- `episodes` needs at least one entry — the AI section must not degrade to
  a bare "nothing recorded" claim when authorized traces exist.
- `derived: false` episode: grounded directly in retained minimal fields.
- `derived: true` episode: a digest of a **fully authorized** (full-read)
  conversation. It must add `"derivation": "host_agent_episode_digest"` and
  `"deniable": true`, list the supporting records in `citations`, and be
  phrased so the student can deny it. Never mark minimal-retention content
  as a full-text digest.
- Under minimal retention, do not reconstruct message content from command
  summaries; say what the retained fields show and stop there.

## `reflection`, `takeaways`, `verification`, `evidence_gaps`

- `reflection` is always `{ "status": "student_authored_only", "text": null }`
  in `working` — the system never ghost-writes reflection. In `submitted`,
  `text` must be the student's own non-empty words.
- `takeaways`: empty array renders as `未记录` in `working`; `submitted`
  requires the student to fill it.
- `verification`: factual bullets about how claims were checked (e.g. test
  logs observed, or the explicit statement that no test log exists).
- `evidence_gaps`: one string per gap; the count feeds `meta.evidence_gaps`.
  A gap is evidence whose absence weakens a conclusion the report wants to
  make — e.g. no test log exists, so verification stays unobserved. Archive
  warnings (unrecognized log formats, skipped records) are already visible
  in the evidence account and do not need to be repeated here; repeat one
  only when it actually blocks a claim.

## Verify red lines

`learntrace verify-narrative <payload> <archive>` rejects the payload when:

1. **红线 1** — any citation does not resolve to an archive observable event;
   and no event ID (`evt-…`) may appear in reader-facing `key_changes`
   text (string form or object `text`), whether bare, wrapped in whitespace
   or backticks, or embedded in a sentence — IDs belong in `citations`.
2. **红线 2** — any body citation (overview/stages/key_changes/merge_anchor/
   turning point/AI episode) references a basis event of a candidate the
   student denied, or a `submitted` payload keeps a
   `denied_kept_in_appendix` turning point.
3. **红线 3** — `meta` counts disagree with the archive, `working` carries a
   reflection text, or `submitted` lacks `intro_note` / `ai_statement` /
   non-empty `takeaways` / student reflection text.

Fix violations by editing the payload, never by weakening a citation to
make the error go away. If the archive is missing evidence you need, record
an evidence gap instead of citing around it.

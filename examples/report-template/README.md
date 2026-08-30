# Report template example

This directory is a copyable template for the report-presentation layer. It shows the shape that a host agent can fill before a CLI renderer produces a learning record.

It is an example, not a complete runnable archive:

- Replace every `<...>` placeholder with evidence from an authorized archive.
- Do not invent event IDs, student statements, test results, commands, patches, or tool output.
- Keep Git stages and AI trace work segments separate.
- Keep automatic summaries marked as derived and deniable.
- Use `未记录` when the evidence does not contain an answer.

Files:

- `narrative-payload.json.example`: payload structure for a working report.
- `learning-record.md.example`: human-readable report structure.

The current v0 production narrative schema still uses `ai_collaboration.episodes`. The `observed_touchpoints` and `work_segments` fields shown here are the report-golden presentation target; they are not yet a production CLI input contract. See `tests/fixtures/report-golden/README.md` for the boundary.

## How to fill it

1. Obtain explicit authorization for the project files, logs, and trace exports.
2. Replace the placeholders only with facts supported by those sources.
3. Put directly observed questions and tool errors in `observed_touchpoints`.
4. Put conservatively grouped activity in `work_segments`.
5. Cite source events in JSON and define the same citations in the report footnotes.
6. Keep learning takeaways and reflection empty until the student writes or confirms them.

A work segment describes an observable process. It does not prove a learning outcome and does not connect an AI trace to a Git commit by time proximity or path overlap.

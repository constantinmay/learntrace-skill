# Task 4 Logic Tree

This file is the one-page logic tree for LearnTrace Task 4. It is meant to be
included in the Task 4 PR so reviewers can understand the end-to-end flow
without reconstructing it from the code.

## One-Line Summary

Task 4 turns local project evidence into an auditable learning archive: it
collects observable facts, proposes candidate learning moments, separates
student confirmation from system inference, and renders both human-readable and
machine-readable outputs.

## Logic Tree

```text
Task 4: Learning Archive
|
+-- 1. Goal
|   |
|   +-- Build a traceable learning portfolio from local evidence
|   +-- Show collaboration and learning, not authorship or cheating judgment
|   +-- Keep facts, inference, and student confirmation strictly separated
|
+-- 2. Inputs
|   |
|   +-- Observable facts
|   |   |
|   |   +-- git_commit
|   |   +-- document
|   |   +-- test_log
|   |   +-- trace_record
|   |
|   +-- Optional student confirmation records
|   |
|   +-- Optional parser warnings
|   |
|   +-- Input source shape
|       |
|       +-- single LearnTrace record JSON
|       +-- JSON container with events / confirmations / warnings
|       +-- hand-authored demo bundle
|
+-- 3. Loading Layer
|   |
|   +-- Walk project directory for JSON files
|   +-- Skip noise directories like .git / .venv / node_modules
|   +-- Parse records into typed models
|   +-- Validate records against schema contracts
|   +-- Reject malformed or conflicting evidence early
|
+-- 4. Inference Layer
|   |
|   +-- Select inferencer
|   |   |
|   |   +-- no LLM env -> deterministic stub inferencer
|   |   +-- LLM env present -> OpenAI-compatible LLM inferencer
|   |
|   +-- Produce candidate learning nodes
|   |   |
|   |   +-- follow_up
|   |   +-- revise_ai_suggestion
|   |   +-- fix_failed_approach
|   |   +-- add_tests
|   |   +-- adjust_constraints
|   |
|   +-- Every candidate must include
|       |
|       +-- node_type
|       +-- candidate statement
|       +-- basis_event_ids
|       +-- uncertainty label
|       +-- question_to_student
|
+-- 5. Trust Boundary
|   |
|   +-- Candidate inference is not treated as fact
|   +-- basis_event_ids must resolve to real observable events
|   +-- Invalid LLM outputs are dropped instead of polluting the archive
|   +-- Student decisions are stored separately from system candidates
|
+-- 6. Confirmation Layer
|   |
|   +-- Candidate status starts as proposed
|   +-- StudentConfirmation can mark a candidate as
|   |   |
|   |   +-- confirmed
|   |   +-- supplemented
|   |   +-- denied
|   |
|   +-- A candidate becomes resolved only when confirmation exists
|
+-- 7. Archive Assembly
|   |
|   +-- Deduplicate events
|   +-- Deduplicate confirmations
|   +-- Deduplicate warnings
|   +-- Materialize stable candidate IDs
|   +-- Validate the whole bundle again
|
+-- 8. Outputs
|   |
|   +-- Markdown learning record
|   |   |
|   |   +-- project overview
|   |   +-- audit summary
|   |   +-- evidence layers
|   |   +-- AI usage
|   |   +-- key candidate decisions
|   |   +-- supporting evidence
|   |   +-- reflection
|   |   +-- next-step questions
|   |
|   +-- Machine-readable archive JSON
|   |   |
|   |   +-- events
|   |   +-- candidates
|   |   +-- confirmations
|   |   +-- warnings
|   |   +-- pending_questions
|   |   +-- source_index
|   |   +-- candidate_links
|   |   +-- record_counts
|   |   +-- risk_flags
|   |   +-- quality_checks
|   |   +-- archive_manifest
|   |
|   +-- Pending-question Markdown
|
+-- 9. Auditability
|   |
|   +-- Stable SHA-256 content fingerprint
|   +-- Record-level hashes
|   +-- Provenance index from source refs to events and candidates
|   +-- Deterministic stub path for tests
|
+-- 10. Safety Constraints
    |
    +-- Do not execute user repository code
    +-- Do not infer cheating or authorship
    +-- Do not turn system guesses into conclusions
    +-- Do not invent missing evidence
    +-- Read API keys only from environment variables
```

## Review Checklist

- Does the archive start from observable facts instead of direct conclusions?
- Can each candidate be traced back to concrete `basis_event_ids`?
- Are student confirmations stored separately from system inference?
- Does the output include both human-readable and machine-readable forms?
- Does the archive remain auditable when the LLM path is disabled?

## PR Note

If this file is included in the PR, reviewers can use it as the top-level map
for the following implementation areas:

- input loading and contract validation
- candidate inference
- confirmation handling
- archive serialization and audit metadata
- Markdown reporting and demo flow

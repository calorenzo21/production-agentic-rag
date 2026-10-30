---
name: dev-log
description: Write the work just finished (a ticket or a whole module) as a lesson in the project's Notion dev log.
disable-model-invocation: true
---

# Dev Log

Turn what was just built into a **lesson**: one entry in the Notion dev log, written in English, while the session that built it is still in context.

The reader is Cesar, months from now, using the log as a guide to building a production agentic RAG system from scratch. The log teaches the **how** and the **why**, not a changelog: every entry teaches how this piece works, why it was built this way rather than another, and the concepts underneath it, deeply enough that Cesar could rebuild it without the course.

**Destination**: data source `collection://e4ec19f1-9148-40e0-a347-2911dbf0f8af` ("Production Agentic RAG — Dev Log"). Fetch it before writing to get the exact property names.

## 1. Pick the branch

The argument names it: `ticket <issue#>` or `module <Wn>`. With no argument, infer it from the session: an `/implement` run is a ticket; a `/retro` that closes a week is a module.

- **Ticket**: one piece of the system, taught in depth.
- **Module**: the week as a whole, taught as architecture: how the pieces fit together and how they plug into the weeks before.

Done when you hold the issue number(s), the commit SHA(s), and the week.

## 2. Gather the facts

- The issue: `gh issue view <n> --json number,title,body,url`. A module also reads every sub-issue under its parent.
- The code: `git log` and `git show <sha>` for each commit, so the lesson explains the code that exists.
- The decisions: `GLOSSARY.md`, any ADR in `docs/adr/` touched by this work, and the session's own reasoning (alternatives weighed, tradeoffs argued, the `/retro` findings for a module).
- The course: the week's snapshot and `notebooks/weekN/README.md`, to say where this build follows the course and where it departs.

Done when every section in step 3 has its material, grounded in the diff, the issue, or the session.

## 3. Write the entry

Create one page in the data source:

- **Name**: `#<issue> <issue title>` for a ticket; `Week <N>: <module title>` for a module.
- **Type**: `Ticket` or `Module`. **Week**: `W<N>`. **Date**: today.
- **Issue**: the issue URL (the parent issue for a module).
- **Commit**: `https://github.com/calorenzo21/production-agentic-rag/commit/<sha>`, using the last commit of the work.

Body sections, in order:

- **What was built**: the behaviour that now exists and where it lives (modules, endpoints, services, containers). A module entry links each ticket entry of that week.
- **How it works**: walk the flow end to end: what enters, which component handles it, what comes out. Draw a Mermaid diagram whenever data moves between components. Quote the few short code excerpts that carry the idea, each followed by what it does.
- **Why this way**: every design decision in the work, each as: the choice, the alternatives, the tradeoff, and why this one won here. Mark where this build departs from the course and why. Link the ADR when one exists.
- **Concepts**: each concept this work introduced (e.g. BM25, RRF, section-aware chunking, LangGraph state), taught from first principles: the problem it solves, how it works, and where it shows up in this code. Write it so Cesar could explain it back to someone else.
- **Problems and fixes**: each problem hit, as cause → fix → the lesson it leaves. Write `No problems.` when the session had none.

A module entry adds a last section:

- **Build order**: the sequence the week was built in, and why that order: what each step unlocked for the next.

Done when every design decision in the diff has its why, every new concept is taught, and all properties are set. Reply with the entry's link.

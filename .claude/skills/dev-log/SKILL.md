---
name: dev-log
description: Log the work just finished (a ticket or a whole module) as an entry in the project's Notion dev log.
disable-model-invocation: true
---

# Dev Log

Record what was just built as one **entry** in the Notion dev log, written in Spanish, while the session that built it is still in context.

**Destination**: data source `collection://e4ec19f1-9148-40e0-a347-2911dbf0f8af` ("Production Agentic RAG — Dev Log"). Fetch it before writing to get the exact property names.

## 1. Pick the branch

The argument names it: `ticket <issue#>` or `module <Wn>`. With no argument, infer it from the session: an `/implement` run is a ticket; a `/retro` that closes a week is a module.

- **Ticket**: the issue that was implemented and the commits that closed it.
- **Module**: the week's parent issue, every sub-issue under it, and the `/retro` findings.

Done when you hold the issue number(s), the commit SHA(s), and the week.

## 2. Gather the facts

Read the issue with `gh issue view <n> --json number,title,body,url`, and the commits with `git log`. A module reads the parent issue plus its sub-issues. Mine the session for what went wrong and what was new.

Done when every section in step 3 has material from the issue, the diff, or the session, not from memory of the course.

## 3. Write the entry

Create one page in the data source:

- **Name**: `#<issue> <issue title>` for a ticket; `Week <N>: <module title>` for a module.
- **Tipo**: `Ticket` or `Módulo`. **Week**: `W<N>`. **Fecha**: today.
- **Issue**: the issue URL (the parent issue for a module).
- **Commit**: `https://github.com/calorenzo21/production-agentic-rag/commit/<sha>`, using the last commit of the work.

Body, three sections:

- **Qué se construyó**: the behaviour that now exists, in a few bullets, naming the modules and endpoints touched. A module entry links each ticket entry of that week.
- **Aprendizajes**: the concepts this work taught (e.g. RRF, section-aware chunking, LangGraph nodes), each explained in two or three lines a future reader can learn from.
- **Problemas y fixes**: each problem hit, as cause → fix. Write `Sin problemas.` when the session had none.

Done when the page exists with all properties set and all three sections filled. Reply with its link.

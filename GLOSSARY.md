# arXiv Paper Curator

A research assistant that collects academic papers from arXiv and answers research questions about them.

## Language

**Paper**:
An academic work published on arXiv and held by the curator.
_Avoid_: Document, article

**Content**:
The text, sections and references extracted from a Paper's PDF. A Paper can be held without its Content.
_Avoid_: Raw text, parsed content, full text, body

**Version**:
One of a Paper's successive releases on arXiv, numbered v1, v2 and so on. The curator holds each Paper at the latest Version it has ingested.
_Avoid_: Revision, edition

**Category**:
An arXiv subject classification, such as `cs.AI`. Each Paper is listed in one or more Categories.
_Avoid_: Topic, subject, tag

**Ingestion**:
The collection of Papers of a Category from arXiv into the curator.
_Avoid_: Import, sync, crawl, fetch, ingestion pipeline

**Search**:
Finding the Papers the curator holds whose Chunks match a researcher's words or their meaning.
_Avoid_: Lookup

**Index**:
A searchable copy of the Papers the curator holds, made of their Chunks.
_Avoid_: Search engine, document store

**Chunk**:
A part of a Paper's Content that a Search can match on its own, held in the Index together with the Paper's title and abstract. A Paper without Content has one Chunk: its title and abstract.
_Avoid_: Passage, segment, fragment

**Week**:
A stage of the reference course, identified by its tag.
_Avoid_: Milestone, phase, sprint

**Deviation**:
A deliberate, recorded difference from the reference course.
_Avoid_: Customisation, divergence, fork

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
An arXiv subject classification that decides which Papers the curator collects, such as `cs.AI`.
_Avoid_: Topic, subject, tag

**Ingestion**:
The collection of Papers of a Category from arXiv into the curator.
_Avoid_: Import, sync, crawl, fetch

**Week**:
A stage of the reference course, identified by its tag.
_Avoid_: Milestone, phase, sprint

**Deviation**:
A deliberate, recorded difference from the reference course.
_Avoid_: Customisation, divergence, fork

# A Paper is identified by its arXiv ID without its Version

A Paper is the work, not one of its Versions, but the course identifies it by its arXiv ID with its Version, such as `2611.23615v1`, so ingesting a day again after one of its Papers gains a Version holds that Paper twice. So this repository identifies a Paper by its arXiv ID without its Version, `2611.23615`, keeps the Version it holds apart, and holds each Paper at the latest Version it has ingested, with that Version's Content when Docling can parse it. ADR 0002 makes the course's code the default, and this changes the course's identity of a Paper, so the README records it as an error of the course fixed.

## Considered Options

- **Keep the course's ID and match Papers on another column without the Version**: rejected because a Paper would have two IDs, of which the one called `arxiv_id` changes with every Version. The course's later Weeks also find a Paper's chunks in OpenSearch by its exact `arxiv_id`, so indexing a new Version would leave the old one's chunks behind.
- **Hold every Version as a Paper of its own**: rejected because it contradicts what a Paper is. Keeping every Version's Content is out of scope.

## Consequences

The migration that makes the change merges the Papers already held twice, and its downgrade cannot restore them. A link built from `arxiv_id` leads to the latest Version on arXiv, which may be newer than the one held, unless it adds the Version. A Paper whose new Version fails to parse keeps the previous Version's Content, and nothing marks it. Code from the course's later Weeks that checks for a Version works unchanged, as each of those checks does nothing on an ID without one.

From Week 3, the course's DAG indexes the Papers whose rows were created on the day it runs. A new Version updates its Paper's row, created with an earlier Version, so that selection would miss it. The DAG indexes instead the Papers its Ingestion stored, by their arXiv IDs.

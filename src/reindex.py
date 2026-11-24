"""Rebuilds the Index from every Paper the curator holds. `make reindex` runs it in the API container."""

from src.db.factory import make_database
from src.services.paper_indexer import make_paper_indexer


def main() -> None:
    database = make_database()
    try:
        counts = make_paper_indexer(database).rebuild_index()
    finally:
        database.teardown()
    print(f"Indexed {counts['papers_indexed']} of the {counts['papers_held']} Papers the curator holds")


if __name__ == "__main__":
    main()

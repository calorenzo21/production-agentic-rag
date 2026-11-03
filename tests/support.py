from pydantic import SecretStr

from paper_curator.settings import OllamaSettings, OpenSearchSettings, PostgresSettings, Settings

# Nothing listens on port 9 (discard), so a real check against it reports the dependency as down.
UNREACHABLE_PORT = 9


def unreachable_settings() -> Settings:
    return Settings(
        postgres=PostgresSettings(
            host="127.0.0.1",
            port=UNREACHABLE_PORT,
            user="curator",
            password=SecretStr("not-a-real-password"),
            database="paper_curator",
        ),
        opensearch=OpenSearchSettings(
            url=f"http://127.0.0.1:{UNREACHABLE_PORT}",
            username="curator",
            password=SecretStr("not-a-real-password"),
        ),
        ollama=OllamaSettings(url=f"http://127.0.0.1:{UNREACHABLE_PORT}"),
    )

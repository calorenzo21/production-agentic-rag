# Follow the course, with recorded Deviations

The foundation built for ADR 0001 outgrew what was needed to learn from the course: a hardened production mode, a proxy, backups and heavy CI, on top of a stack that shares little structure with the material being studied. So this repository now mirrors the course Week by Week: its layout, Compose services, versions, tooling and application code are the course's by default, starting from the `week1.0` tag. Anything else is a Deviation, recorded in the README with its reason. The previous foundation is kept at the `foundation-v1` tag.

The accepted Deviations are native Ollama, so that inference can use the GPU, plus security fixes and corrections of the course's own errors. Security stays even where it costs fidelity, because the course's defaults include known passwords and an open search engine, and those are not worth learning from or inheriting.

## Considered Options

- **Keep the foundation and add the course's features to it**: rejected because every Week would first need translating into a different layout and different versions, such as Airflow 3 instead of 2.
- **Start the course's final state at once**: rejected because the final Week brings 13 services and hides how the base was built.
- **Follow the course with no security changes**: rejected despite being the most faithful option, because it would run unauthenticated services with known passwords.
- **OpenSearch with the image's demo security**: rejected because its certificates and secondary users' passwords are public. A generated CA and users cost more setup but give real verification.
- **Isolate Airflow from the application's data**: rejected because the course's ingestion DAGs write Papers directly to the application database. Only Airflow's own metadata is separated.

## Consequences

The repository no longer claims to be production-ready. Code taken from later Weeks of the course will not work unchanged where a Deviation touches it: from Week 3, the course's OpenSearch client needs TLS and authentication, and new tables need a migration instead of `create_all`. ADR 0003 later moves Airflow to version 3.

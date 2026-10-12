# Foundation Architecture Decisions

## Decision 1 — One execution runtime

Interactive requests, scheduled commitments, Heartbeat-triggered work, retries, and manual resumes must receive the same capability catalog, tenant identity, policy context, model router, checkpointer, and event contract. A worker may differ in process topology, but not in available business capabilities or execution semantics.

## Decision 2 — Artifacts are typed binary resources

A generated report is not a chat string and not a JSON object. It is a typed artifact with immutable bytes, a canonical filename, a canonical media type, a checksum, tenant ownership, provenance, and a download endpoint. The artifact event contains metadata only. The browser must download artifact bytes through an authenticated binary request, validate the response contract, and construct a local blob with the canonical media type and filename.

```text
Model result → Artifact policy → Artifact service → immutable bytes + metadata
      → task event contains ArtifactDescriptor → authenticated binary fetch → Blob download
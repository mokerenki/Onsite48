"""
Manual test for memory ingestion and retrieval.
Run intentionally inside Docker:
  docker compose run --rm backend python -m tests.manual.memory_diagnostic
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.memory.cognee_setup import CogneeMemory
from app.memory.domain_schemas import ALL_DOMAIN_SCHEMAS
import structlog

structlog.configure(
    processors=[
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer()
    ]
)

logger = structlog.get_logger("test_memory")


async def run_memory_diagnostic():
    """Ingest sample text per domain and verify semantic + graph retrieval."""

    domains = {
        "sales": {
            "schema": ALL_DOMAIN_SCHEMAS["sales"],
            "content": "Acme Corp signed a $500,000 deal with Jane Doe. The opportunity is in the Negotiation stage and closes on 2025-12-31.",
            "expected_entities": ["Account", "Opportunity"],
        },
        "marketing": {
            "schema": ALL_DOMAIN_SCHEMAS["marketing"],
            "content": "The 'Summer Sale' email campaign sent to 10,000 subscribers had a 25% open rate and 5% click rate.",
            "expected_entities": ["Campaign", "Email"],
        },
        "finance": {
            "schema": ALL_DOMAIN_SCHEMAS["finance"],
            "content": "Invoice #INV-2025-001 for $1,200 was paid on 2025-01-15 via credit card.",
            "expected_entities": ["Invoice", "Payment"],
        },
        "executive": {
            "schema": ALL_DOMAIN_SCHEMAS["executive"],
            "content": "Revenue KPI reached $2.5M in Q4 2025, exceeding the $2.0M target.",
            "expected_entities": ["KPI", "Goal"],
        },
        "product": {
            "schema": ALL_DOMAIN_SCHEMAS["product"],
            "content": "The new 'AI Assistant' feature is planned for Q2 2026 release with priority P0.",
            "expected_entities": ["Product", "Feature", "Roadmap"],
        },
        "healthcareadmin": {
            "schema": ALL_DOMAIN_SCHEMAS["healthcareadmin"],
            "content": "Patient John Smith (DOB 1980-05-10) had a claim #CLM-2025-003 denied due to missing prior authorization.",
            "expected_entities": ["Patient", "Claim", "PriorAuthorization"],
        },
    }

    for domain, data in domains.items():
        logger.info("testing_domain", domain=domain)

        memory = CogneeMemory(config={
            "qdrant_host": os.getenv("QDRANT_HOST", "localhost"),
            "qdrant_port": int(os.getenv("QDRANT_PORT", 6333)),
            "kuzu_db_path": os.getenv("KUZU_DB_PATH", "/tmp/aether/kuzu.db"),
            "schema": data["schema"],
        })
        await memory.initialize()
        logger.info("memory_initialized", domain=domain)

        event = {
            "content": data["content"],
            "source": "test",
            "domain": domain,
            "event_id": f"test_{domain}",
        }
        await memory.ingest(event)
        logger.info("ingested", domain=domain)

        result = await memory.search(
            query=data["content"][:80],
            mode="semantic",
            top_k=3,
        )
        logger.info("semantic_results", domain=domain, count=len(result.get("results", [])))

        entity_type = data["expected_entities"][0]
        cypher = f"MATCH (n:{entity_type}) RETURN n.id AS id LIMIT 5"
        graph_result = await memory.search(query=cypher, mode="graph")
        logger.info("graph_results", domain=domain, entity_type=entity_type, count=len(graph_result.get("results", [])))

        await memory.close()
        logger.info("domain_test_complete", domain=domain)

    logger.info("test_complete")


if __name__ == "__main__":
    asyncio.run(run_memory_diagnostic())
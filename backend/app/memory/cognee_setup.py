import asyncio
import json
import hashlib
from app.memory.temporal_setup import TemporalGraph
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import structlog

# Qdrant Client
from qdrant_client import QdrantClient, models

# KuzuDB Client
import kuzu

# OpenAI for Embeddings and LLM for Entity Extraction
from openai import AsyncOpenAI

# Local imports
from app.memory.graph_setup import KuzuGraph
from app.memory.domain_schemas import EXECUTIVE_SCHEMA
from app.core.memory_contracts import expiry_epoch, memory_write_decision

# Redis for embedding cache
import redis.asyncio as aioredis

logger = structlog.get_logger("aether.memory.cognee_setup")

EMBEDDING_CACHE_TTL_SECONDS = 7 * 24 * 3600  # 7 days


class CogneeMemory:
    """Orchestrates memory operations: ingestion, embedding, vector store (Qdrant), and graph store (KuzuDB) interaction."""

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self._initialized = False
        self._schema = self.config.get("schema", EXECUTIVE_SCHEMA)
        
        self.qdrant_client: Optional[QdrantClient] = None
        self.kuzu_graph: Optional[KuzuGraph] = None
        self.openai_client: Optional[AsyncOpenAI] = None
        self._redis_client: Optional[aioredis.Redis] = None  # <-- NEW

        self.qdrant_collection_name = self.config.get("qdrant_collection_name", "aether_memory")
        self.embedding_model_name = self.config.get(
            "embedding_model_name",
            os.environ.get("OPENAI_EMBEDDING_MODEL", "nvidia/nv-embed-v1")
        )
        self.llm_extraction_model_name = self.config.get(
            "llm_extraction_model_name",
            os.environ.get("OPENAI_CHAT_MODEL", "meta/llama-3.3-70b-instruct")
        )
        self.embedding_dim = self.config.get("embedding_dim", 4096)
        self._legacy_purge_marker = self.config.get("legacy_purge_marker", "/tmp/onsite48/memory_scope_purge_v1.complete")

    def _legacy_purge_requested(self) -> bool:
        return os.getenv("MEMORY_PURGE_LEGACY_UNSCOPED", "false").strip().lower() == "true"

    @property
    def memory_available(self) -> bool:
        """Whether long-term memory can actually answer a query right now.

        Memory is an optional capability, so callers use this to degrade to a
        plain answer instead of failing a task over an unavailable store.
        """
        return bool(self._initialized and self.qdrant_client is not None)

    def _qdrant_connect_attempts(self) -> int:
        try:
            attempts = int(os.getenv("MEMORY_QDRANT_CONNECT_ATTEMPTS", "6"))
        except ValueError:
            attempts = 6
        return max(1, attempts)

    async def _connect_qdrant(self) -> None:
        """Connect to Qdrant, retrying while it finishes loading.

        Qdrant accepts connections well before it can serve requests, so a
        single attempt at startup turns a normal cold start into a permanent
        ``memory: degraded`` until the process is restarted. The backoff is
        bounded and the caller still gets a raised error, so a genuinely absent
        Qdrant degrades the capability instead of hanging startup forever.
        """
        attempts = self._qdrant_connect_attempts()
        base_delay = float(self.config.get("qdrant_retry_base_delay", 0.5))
        last_error: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            try:
                self.qdrant_client = QdrantClient(
                    host=self.config.get("qdrant_host", "localhost"),
                    port=self.config.get("qdrant_port", 6333)
                )
                # The client is lazy, so this is the first call that can fail.
                self._ensure_collection()
                if attempt > 1:
                    logger.info("qdrant.connected_after_retry", attempts=attempt)
                return
            except Exception as exc:
                last_error = exc
                self.qdrant_client = None
                if attempt >= attempts:
                    break
                delay = min(base_delay * (2 ** (attempt - 1)), 8.0)
                logger.warning(
                    "qdrant.connect_retry",
                    attempt=attempt,
                    attempts=attempts,
                    delay=round(delay, 2),
                    error=type(exc).__name__,
                )
                await asyncio.sleep(delay)
        raise RuntimeError(
            f"qdrant unavailable after {attempts} attempt(s): {type(last_error).__name__}"
        ) from last_error

    def _ensure_collection(self) -> None:
        """Create the collection once; never erase live memory during startup."""
        assert self.qdrant_client is not None
        exists = self.qdrant_client.collection_exists(self.qdrant_collection_name)
        if exists and self._legacy_purge_requested() and not os.path.exists(self._legacy_purge_marker):
            logger.warning("qdrant.legacy_unscoped_memory_purge_started", collection=self.qdrant_collection_name)
            self.qdrant_client.delete_collection(self.qdrant_collection_name)
            exists = False
            os.makedirs(os.path.dirname(self._legacy_purge_marker), exist_ok=True)
            with open(self._legacy_purge_marker, "w", encoding="utf-8") as marker:
                marker.write(datetime.now(timezone.utc).isoformat())
        if not exists:
            self.qdrant_client.create_collection(
                collection_name=self.qdrant_collection_name,
                vectors_config=models.VectorParams(size=self.embedding_dim, distance=models.Distance.COSINE),
            )
            logger.info("qdrant.collection_created", collection=self.qdrant_collection_name)
        else:
            logger.info("qdrant.collection_preserved", collection=self.qdrant_collection_name)
        for field_name in ("organization_id", "user_id", "thread_id"):
            try:
                self.qdrant_client.create_payload_index(
                    collection_name=self.qdrant_collection_name,
                    field_name=field_name,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
            except Exception as exc:
                logger.warning("qdrant.payload_index_unavailable", field=field_name, error=str(exc)[:160])

    async def initialize(self):
        """Connects to Qdrant, KuzuDB, Redis, and initializes LLM clients, setting up schemas."""
        if self._initialized:
            logger.info("cognee_memory.already_initialized")
            return

        logger.info("cognee_memory.initializing")
        try:
            # Initialize Qdrant Client
            await self._connect_qdrant()
            logger.info("qdrant.client_initialized", collection=self.qdrant_collection_name)

            # Initialize KuzuDB Graph
            kuzu_db_path = self.config.get("kuzu_db_path", "/tmp/aether/kuzu.db")
            self.kuzu_graph = KuzuGraph(db_path=kuzu_db_path)
            self.kuzu_graph.initialize(schema=self._schema)
            logger.info("kuzu_graph.initialized", db_path=kuzu_db_path)

            # Initialize OpenAI Client
            openai_api_key = os.environ.get("OPENAI_API_KEY")
            if not openai_api_key:
                raise RuntimeError("OPENAI_API_KEY is required for CogneeMemory embeddings and entity extraction.")
            openai_api_base = os.environ.get("OPENAI_API_BASE")
            self.openai_client = AsyncOpenAI(
                api_key=openai_api_key,
                base_url=openai_api_base
            )
            logger.info("openai.client_initialized")

            # Initialize Redis for embedding cache  <-- NEW
            redis_url = self.config.get("redis_url")
            if redis_url:
                self._redis_client = aioredis.from_url(redis_url)
                await self._redis_client.ping()
                logger.info("embedding_cache.redis_connected", redis_url=redis_url)

            self._initialized = True
            logger.info("cognee_memory.initialized_success")
        except Exception as e:
            # Never leave a half-initialized instance: `memory_available` reads
            # `_initialized`, so a later query cannot mistake a store that never
            # came up for a working one. A Qdrant client is only discarded when
            # Qdrant itself is what failed.
            self._initialized = False
            logger.error("cognee_memory.initialization_failed", error=str(e), exc_info=True)
            raise

    # ── Embedding Cache Helpers ───────────────────────────────────

    def _embedding_cache_key(self, text: str) -> str:
        """Deterministic cache key for an embedding payload."""
        payload = f"{self.embedding_model_name}:{text}"
        return f"aether:embedding:{hashlib.sha256(payload.encode()).hexdigest()}"

    async def _get_cached_embedding(self, text: str) -> Optional[List[float]]:
        """Fetch embedding from Redis cache if available."""
        if not self._redis_client:
            return None
        key = self._embedding_cache_key(text)
        cached = await self._redis_client.get(key)
        if cached:
            logger.debug("embedding_cache.hit", key=key[:16])
            return json.loads(cached)
        return None

    async def _set_cached_embedding(self, text: str, embedding: List[float]) -> None:
        """Store embedding in Redis cache with TTL."""
        if not self._redis_client:
            return
        key = self._embedding_cache_key(text)
        await self._redis_client.setex(
            key,
            EMBEDDING_CACHE_TTL_SECONDS,
            json.dumps(embedding),
        )
        logger.debug("embedding_cache.set", key=key[:16])

    async def _generate_embedding(self, text: str) -> List[float]:
        """Generate embedding with caching layer."""
        # 1. Check cache
        cached = await self._get_cached_embedding(text)
        if cached is not None:
            return cached

        # 2. Call API
        embedding_response = await self.openai_client.embeddings.create(
            input=text,
            model=self.embedding_model_name
        )
        embedding = embedding_response.data[0].embedding

        # 3. Store in cache
        await self._set_cached_embedding(text, embedding)
        return embedding

    # ── Ingest ────────────────────────────────────────────────────

    @staticmethod
    def _scope_from_event(event: Dict[str, Any]) -> Dict[str, str]:
        scope = {
            name: str(value).strip()
            for name in ("organization_id", "user_id", "thread_id")
            if (value := event.get(name)) is not None
        }
        missing = [name for name in ("organization_id", "user_id", "thread_id") if not scope.get(name)]
        if missing:
            raise ValueError("Memory ingestion requires " + ", ".join(missing) + ".")
        return scope

    @staticmethod
    def _scoped_graph_id(scope: Dict[str, str], value: str) -> str:
        return ":".join((scope["organization_id"], scope["user_id"], scope["thread_id"], value))

    async def ingest(self, event: Dict[str, Any]):
        """Ingests an event, generates embeddings, extracts entities/relationships, and updates vector + graph stores.

        Every write passes the typed memory policy first. An event that would
        store an unapproved preference, or a low-confidence claim with no
        supporting evidence, is refused and logged rather than silently
        becoming durable truth.
        """
        if not self._initialized:
            await self.initialize()

        record, refusal = memory_write_decision(event)
        if record is None:
            logger.warning(
                "cognee_memory.ingest_refused",
                event_id=event.get("event_id") if isinstance(event, dict) else None,
                reason=refusal,
            )
            return
        event = {**event, **record.model_dump()}

        content = record.value
        if not content:
            logger.warning("cognee_memory.ingest_empty_content", event_id=event.get("event_id"))
            return

        scope = self._scope_from_event(event)
        event_id = event.get("event_id", str(uuid.uuid4()))
        timestamp = event.get("timestamp", datetime.now(timezone.utc).isoformat())
        source = record.source

        logger.info("cognee_memory.ingesting_event", event_id=event_id, content_len=len(content), source=source, layer=record.layer.value)

        try:
            # 1. Generate embeddings (with cache)
            embedding = await self._generate_embedding(content)
            logger.debug("cognee_memory.embedding_generated", event_id=event_id)

            # 2. Store in Qdrant
            qdrant_point = models.PointStruct(
                id=str(uuid.UUID(event_id)),
                vector=embedding,
                payload={
                    "content": content,
                    "timestamp": timestamp,
                    "source": source,
                    "event_id": event_id,
                    # Provenance and lifecycle travel with the record, so
                    # retrieval can tell a preference from an observation and
                    # can drop anything that has gone stale.
                    "layer": record.layer.value,
                    "confidence": record.confidence.value,
                    "user_approved": record.user_approved,
                    "evidence_ids": record.evidence_ids,
                    "task_id": record.task_id,
                    "expires_at": record.expires_at,
                    # Numeric companion to expires_at: Qdrant range filters are
                    # numeric only, so the ISO string cannot be range-queried.
                    "expires_at_epoch": expiry_epoch(record.expires_at),
                    **scope,
                }
            )
            operation_info = self.qdrant_client.upsert(
                collection_name=self.qdrant_collection_name,
                points=[qdrant_point],
                wait=True
            )
            logger.debug("qdrant.upsert_complete", event_id=event_id, status=operation_info.status.name)

            # 3. Extract entities and relationships
            extracted_data = await self._extract_entities_and_relationships_llm(content)
            entities = extracted_data.get("entities", [])
            relationships = extracted_data.get("relationships", [])
            logger.debug("cognee_memory.llm_extraction_complete", event_id=event_id, entities_count=len(entities), relationships_count=len(relationships))

            # 4. Store in KuzuDB
            await self._update_kuzu_graph(event_id, content, entities, relationships, timestamp, scope)

            logger.info("cognee_memory.ingestion_complete", event_id=event_id, entities_count=len(entities), relationships_count=len(relationships))
        except Exception as e:
            logger.error("cognee_memory.ingestion_failed", event_id=event_id, error=str(e), exc_info=True)
            raise

    async def _extract_entities_and_relationships_llm(self, text: str) -> Dict[str, Any]:
        """Extracts entities and relationships from text using an LLM, guided by the configured schema."""
        schema_description = json.dumps(self._schema, indent=2)
        prompt = f"""You are an expert knowledge graph extractor. Your task is to identify entities and relationships from the provided text based on the following schema. 
        Extract only entities and relationships explicitly defined in the schema. If an entity or relationship type is not in the schema, do not extract it.
        
        Schema:
        ```json
        {schema_description}
        ```

        Text to analyze:
        {text}

        Output your response as a JSON object with two keys: "entities" (a list of objects, each with "type", "id", and "properties") and "relationships" (a list of objects, each with "type", "source_id", "target_id", and "properties").
        For entity IDs, use a stable identifier from the text if available (e.g., case number, document ID), otherwise generate a UUID. Ensure IDs are unique within their type.
        Example Entity: {{
        "type": "Case", "id": "CASE-2023-001", "properties": {{"case_number": "CASE-2023-001", "status": "Open"}}}}
        Example Relationship: {{"type": "HAS_DOCUMENT", "source_id": "CASE-2023-001", "target_id": "DOC-456", "properties": {{"filed_date": "2023-01-15"}}}}
        """

        try:
            response = await self.openai_client.chat.completions.create(
                model=self.llm_extraction_model_name,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that extracts structured data from text."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.1
            )
            content = response.choices[0].message.content
            if content:
                return json.loads(content)
            return {"entities": [], "relationships": []}
        except Exception as e:
            logger.error("cognee_memory.llm_extraction_failed", error=str(e), exc_info=True)
            return {"entities": [], "relationships": []}

    async def _update_kuzu_graph(self, event_id: str, content: str, entities: List[Dict[str, Any]], relationships: List[Dict[str, Any]], timestamp: str, scope: Dict[str, str]):
        """Updates the KuzuDB graph with extracted entities and relationships, including temporal metadata."""
        if not self.kuzu_graph:
            logger.error("kuzu_graph.not_initialized_for_update")
            return

        doc_id = self._scoped_graph_id(scope, f"DOC-{event_id}")
        self.kuzu_graph.add_node(
            label="MemoryDocument",
            properties={
                "id": doc_id,
                "content_summary": content[:200],
                "full_content_qdrant_id": str(uuid.UUID(event_id)),
                "timestamp": timestamp,
                "valid_from": timestamp,
                "valid_to": datetime.max.replace(tzinfo=timezone.utc).isoformat(),
                **scope,
            }
        )

        for entity in entities:
            entity_type = entity.get("type")
            entity_id = entity.get("id")
            properties = entity.get("properties", {})
            if entity_type and entity_id:
                scoped_entity_id = self._scoped_graph_id(scope, str(entity_id))
                properties = {**properties, "id": scoped_entity_id, **scope}
                properties["valid_from"] = properties.get("valid_from", timestamp)
                properties["valid_to"] = properties.get("valid_to", datetime.max.replace(tzinfo=timezone.utc).isoformat())
                self.kuzu_graph.add_node(label=entity_type, properties=properties)
                # Entity references remain scope-prefixed. A generic document-to-
                # any-entity relation is not representable safely across Kuzu's
                # typed relationship tables, so scoped document retrieval is the
                # isolation boundary for this phase.

        for rel in relationships:
            rel_type = rel.get("type")
            source_id = rel.get("source_id")
            target_id = rel.get("target_id")
            properties = rel.get("properties", {})
            if rel_type and source_id and target_id:
                properties["valid_from"] = properties.get("valid_from", timestamp)
                properties["valid_to"] = properties.get("valid_to", datetime.max.replace(tzinfo=timezone.utc).isoformat())
                source_label = self._get_entity_label_from_id(source_id)
                target_label = self._get_entity_label_from_id(target_id)
                if source_label and target_label:
                    scoped_source_id = self._scoped_graph_id(scope, str(source_id))
                    scoped_target_id = self._scoped_graph_id(scope, str(target_id))
                    properties = {**properties, **scope}
                    self.kuzu_graph.add_edge(from_label=source_label, from_id=scoped_source_id, to_label=target_label, to_id=scoped_target_id, rel_type=rel_type, properties=properties)
                else:
                    logger.warning("cognee_memory.cannot_infer_labels", source_id=source_id, target_id=target_id, rel_type=rel_type)

    def _get_entity_label_from_id(self, entity_id: str) -> Optional[str]:
        """Helper to infer entity label from its ID."""
        for entity_name, entity_def in self._schema.get("entities", {}).items():
            if entity_name.upper() in entity_id.upper():
                return entity_name
        return None

    async def search(self, query: str, mode: str = "semantic", top_k: int = 5, 
                     entity_label: Optional[str] = None, entity_id: Optional[str] = None, property_name: Optional[str] = None, 
                     query_time: Optional[datetime] = None, organization_id: Optional[str] = None,
                     user_id: Optional[str] = None, thread_id: Optional[str] = None) -> Dict[str, Any]:
        """Performs semantic, graph, or temporal memory retrieval."""
        if not self._initialized:
            await self.initialize()

        scope = self._scope_from_event({
            "organization_id": organization_id,
            "user_id": user_id,
            "thread_id": thread_id,
        })
        if mode == "semantic":
            # Scope filtering is mandatory and is applied server-side on every search.
            query_embedding = await self._generate_embedding(query)
            scope_filter = models.Filter(
                must=[
                    models.FieldCondition(key=name, match=models.MatchValue(value=value))
                    for name, value in scope.items()
                ],
                # Expire stale memory at read time. Records with no expiry have
                # no expires_at_epoch value, so they do not match the range
                # condition and are kept.
                must_not=[
                    models.FieldCondition(
                        key="expires_at_epoch",
                        range=models.Range(lt=datetime.now(timezone.utc).timestamp()),
                    )
                ],
            )
            search_result = self.qdrant_client.search(
                collection_name=self.qdrant_collection_name,
                query_vector=query_embedding,
                query_filter=scope_filter,
                limit=top_k,
                with_payload=True
            )
            return {"mode": "semantic", "query": query, "scope": scope, "results": [hit.payload for hit in search_result]}
        
        elif mode == "graph":
            if not self.kuzu_graph:
                raise ValueError("KuzuGraph not initialized for graph query.")
            # Direct raw Cypher cannot safely be trusted with tenant constraints.
            # Return only scoped document metadata through a fixed server-owned query.
            escaped = {key: value.replace("'", "\\\\'") for key, value in scope.items()}
            scoped_query = (
                "MATCH (d:MemoryDocument) WHERE "
                f"d.organization_id = '{escaped['organization_id']}' AND "
                f"d.user_id = '{escaped['user_id']}' AND d.thread_id = '{escaped['thread_id']}' "
                f"RETURN d LIMIT {max(1, min(int(top_k), 50))}"
            )
            results = self.kuzu_graph.query(scoped_query)
            return {"mode": "graph", "query": query, "scope": scope, "results": results}
        
        elif mode == "temporal":
            if not entity_id or not property_name or not query_time:
                raise ValueError("entity_id, property_name, and query_time are required for temporal mode.")
            if not self.kuzu_graph:
                raise ValueError("KuzuGraph not initialized for temporal query.")
            
            temporal_graph = TemporalGraph(self.kuzu_graph)
            scoped_entity_id = self._scoped_graph_id(scope, entity_id)
            result = await temporal_graph.query_at_time(scoped_entity_id, property_name, query_time, entity_label=entity_label)
            return {
                "mode": "temporal",
                "entity_label": entity_label,
                "entity_id": entity_id,
                "scope": scope,
                "property_name": property_name,
                "query_time": query_time.isoformat(),
                "result": result,
            }
        
        else:
            raise ValueError(f"Unsupported memory retrieval mode: {mode}")

    async def delete_scope(
        self,
        *,
        organization_id: str,
        user_id: str,
        thread_id: str | None = None,
        event_ids: list[str] | None = None,
    ) -> Dict[str, Any]:
        """Delete only memory owned by the supplied tenant/user/thread scope."""
        if not self._initialized:
            await self.initialize()
        scope = self._scope_from_event({
            "organization_id": organization_id,
            "user_id": user_id,
            "thread_id": thread_id or "scope-wide",
        })
        must = [models.FieldCondition(key=name, match=models.MatchValue(value=value)) for name, value in scope.items()]
        if event_ids:
            must.append(models.FieldCondition(key="event_id", match=models.MatchAny(any=[str(item) for item in event_ids])))
        result = self.qdrant_client.delete(
            collection_name=self.qdrant_collection_name,
            points_selector=models.FilterSelector(filter=models.Filter(must=must)),
            wait=True,
        )
        deleted_graph = 0
        if self.kuzu_graph and thread_id:
            escaped = {key: str(value).replace("'", "\\'") for key, value in scope.items()}
            query = (
                "MATCH (d:MemoryDocument) WHERE "
                f"d.organization_id = '{escaped['organization_id']}' AND "
                f"d.user_id = '{escaped['user_id']}' AND d.thread_id = '{escaped['thread_id']}' "
                "DETACH DELETE d"
            )
            try:
                self.kuzu_graph.query(query)
                deleted_graph = 1
            except Exception as exc:
                logger.warning("cognee_memory.graph_scope_delete_failed", error=str(exc), scope=scope)
        return {"status": "ok", "scope": scope, "vector_result": str(result), "graph_documents": deleted_graph}

    async def purge_expired(self) -> Dict[str, Any]:
        """Remove expired vector records; reads already hide them immediately."""
        if not self._initialized:
            await self.initialize()
        result = self.qdrant_client.delete(
            collection_name=self.qdrant_collection_name,
            points_selector=models.FilterSelector(filter=models.Filter(
                must=[models.FieldCondition(
                    key="expires_at_epoch",
                    range=models.Range(lt=datetime.now(timezone.utc).timestamp()),
                )]
            )),
            wait=True,
        )
        return {"status": "ok", "vector_result": str(result)}

    async def close(self):
        """Closes all client connections."""
        if self.qdrant_client:
            try:
                self.qdrant_client.close()
            except Exception:
                pass
        if self.kuzu_graph and self.kuzu_graph.conn:
            try:
                self.kuzu_graph.conn.close()
            except Exception:
                pass
        if self.openai_client:
            await self.openai_client.close()
        if self._redis_client:
            await self._redis_client.close()
        self._initialized = False
        logger.info("cognee_memory.closed")
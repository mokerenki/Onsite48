import kuzu
from typing import Any, Dict, List, Optional
import structlog
import os
import json

from app.memory.domain_schemas import EXECUTIVE_SCHEMA

logger = structlog.get_logger("aether.memory.graph_setup")

def _escape_cypher_string(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'")

class KuzuGraph:
    """Manages the KuzuDB embedded knowledge graph, including schema creation and query execution."""

    def __init__(self, db_path: str = "/tmp/aether/kuzu.db"):
        self.db_path = db_path
        self.db: Optional[kuzu.Database] = None
        self.conn: Optional[kuzu.Connection] = None
        logger.info("kuzu_graph.initialized", db_path=db_path)

    def initialize(self, schema: Dict[str, Any] = None):
        """Initializes the KuzuDB database and creates the schema if not already created."""
        if self.conn:
            logger.info("kuzu_graph.already_initialized")
            return

        logger.info("kuzu_graph.connecting", db_path=self.db_path)
        try:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            self.db = kuzu.Database(self.db_path)
            self.conn = kuzu.Connection(self.db)
            self._create_schema(schema or EXECUTIVE_SCHEMA)
            logger.info("kuzu_graph.connected_and_schema_created")
        except Exception as e:
            logger.error("kuzu_graph.initialization_failed", db_path=self.db_path, error=str(e), exc_info=True)
            raise

    def _create_schema(self, schema: Dict[str, Any]):
        """Creates nodes and relationships based on the schema, handling existing tables gracefully."""
        if not schema or "entities" not in schema:
            logger.warning("kuzu_graph.no_schema_defined_or_invalid")
            return

        # Create Node Tables
        for entity_name, entity_def in schema.get("entities", {}).items():
            entity_type = entity_name
            properties = entity_def.get("properties", {})
            if not entity_type:
                continue

            # Exclude 'id' from properties because it's added as PRIMARY KEY separately
            props_without_id = {k: v for k, v in properties.items() if k != "id"}
            properties_str = ", ".join([f"{prop} {ptype}" for prop, ptype in props_without_id.items()])
            # Every new graph node is tenant and thread scoped. Existing shared
            # graphs are deliberately retired during the scope migration rather
            # than altered in place, so these columns are present consistently.
            for scope_field in ("organization_id", "user_id", "thread_id"):
                if scope_field not in props_without_id:
                    properties_str += (", " if properties_str else "") + f"{scope_field} STRING"
            if "valid_from" not in props_without_id:
                properties_str += (", " if properties_str else "") + "valid_from STRING, valid_to STRING"
            # If they are present, they're already included

            if properties_str:
                create_node_query = f"CREATE NODE TABLE {entity_type}(id STRING, {properties_str}, PRIMARY KEY (id))"
            else:
                create_node_query = f"CREATE NODE TABLE {entity_type}(id STRING, PRIMARY KEY (id))"

            try:
                self.conn.execute(create_node_query)
                logger.info("kuzu_graph.node_table_created", table=entity_type)
            except Exception as e:
                if "already exists" in str(e).lower():
                    logger.info("kuzu_graph.node_table_exists", table=entity_type)
                else:
                    logger.warning("kuzu_graph.node_table_creation_failed", table=entity_type, error=str(e), exc_info=True)

        # A dedicated document table is the safe entry point for scoped graph
        # retrieval. It never shares rows between users or task threads.
        try:
            self.conn.execute(
                "CREATE NODE TABLE MemoryDocument("
                "id STRING, organization_id STRING, user_id STRING, thread_id STRING, "
                "content_summary STRING, full_content_qdrant_id STRING, timestamp STRING, "
                "valid_from STRING, valid_to STRING, PRIMARY KEY (id))"
            )
        except Exception as e:
            if "already exists" not in str(e).lower():
                logger.warning("kuzu_graph.memory_document_table_creation_failed", error=str(e), exc_info=True)

        # Create Relationship Tables
        for rel_name, rel_def in schema.get("relationships", {}).items():
            from_type = rel_def.get("from")
            to_type = rel_def.get("to")
            if not rel_name or not from_type or not to_type:
                continue

            # Add tenant/thread and temporal properties to every new relationship.
            rel_properties_str = "organization_id STRING, user_id STRING, thread_id STRING, valid_from STRING, valid_to STRING"
            create_rel_query = f"CREATE REL TABLE {rel_name}(FROM {from_type} TO {to_type}, {rel_properties_str})"
            try:
                self.conn.execute(create_rel_query)
                logger.info("kuzu_graph.rel_table_created", table=rel_name, from_node=from_type, to_node=to_type)
            except Exception as e:
                if "already exists" in str(e).lower():
                    logger.info("kuzu_graph.rel_table_exists", table=rel_name)
                else:
                    logger.warning("kuzu_graph.rel_table_creation_failed", table=rel_name, error=str(e), exc_info=True)

    def add_node(self, label: str, properties: Dict[str, Any]):
        """Adds or updates a node in the graph. Uses MERGE for idempotency."""
        if not self.conn:
            self.initialize()
        
        node_id = properties.get("id")
        if not node_id:
            logger.error("kuzu_graph.add_node_missing_id", label=label, properties=properties)
            return

        props_for_query = {k: v for k, v in properties.items() if k != "id"}
        set_parts = [f"n.id = '{_escape_cypher_string(str(node_id))}'"]
        for k, v in props_for_query.items():
            if isinstance(v, str):
                set_parts.append(f"n.{k} = '{_escape_cypher_string(str(v))}'")
            else:
                set_parts.append(f"n.{k} = {json.dumps(v)}")

        query = f"MERGE (n:{label} {{id: '{_escape_cypher_string(str(node_id))}'}}) SET {', '.join(set_parts)}"
        try:
            self.conn.execute(query)
            logger.debug("kuzu_graph.node_merged", label=label, id=node_id)
        except Exception as e:
            logger.error("kuzu_graph.node_merge_failed", label=label, id=node_id, error=str(e), exc_info=True)

    def add_edge(self, from_label: str, from_id: str, to_label: str, to_id: str, rel_type: str, properties: Dict[str, Any] = None):
        """Adds or updates an edge between two nodes."""
        if not self.conn:
            self.initialize()
        
        rel_properties = properties or {}
        set_parts = []
        for k, v in rel_properties.items():
            if isinstance(v, str):
                set_parts.append(f"r.{k} = '{_escape_cypher_string(str(v))}'")
            else:
                set_parts.append(f"r.{k} = {json.dumps(v)}")

        set_clause = f"SET {', '.join(set_parts)}" if set_parts else ""

        query = f"MATCH (a:{from_label}), (b:{to_label}) WHERE a.id = '{_escape_cypher_string(str(from_id))}' AND b.id = '{_escape_cypher_string(str(to_id))}' MERGE (a)-[r:{rel_type}]->(b) {set_clause}"
        try:
            self.conn.execute(query)
            logger.debug("kuzu_graph.edge_merged", from_id=from_id, to_id=to_id, rel_type=rel_type)
        except Exception as e:
            logger.error("kuzu_graph.edge_merge_failed", from_id=from_id, to_id=to_id, rel_type=rel_type, error=str(e), exc_info=True)

    def query(self, cypher_query: str) -> List[Dict[str, Any]]:
        """Executes a Cypher query and returns results."""
        if not self.conn:
            self.initialize()
        try:
            response = self.conn.execute(cypher_query)
            results = []
            column_names = response.get_column_names()
            for row in response:
                results.append(dict(zip(column_names, row)))
            logger.debug("kuzu_graph.query_executed", query=cypher_query, results_count=len(results))
            return results
        except Exception as e:
            logger.error("kuzu_graph.query_error", query=cypher_query, error=str(e), exc_info=True)
            return []
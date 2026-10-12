from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import structlog

from app.memory.graph_setup import KuzuGraph, _escape_cypher_string

logger = structlog.get_logger("aether.memory.temporal_setup")

class TemporalGraph:
    """Provides an abstraction for managing and querying temporal facts within the KuzuDB graph."""

    def __init__(self, kuzu_graph: KuzuGraph):
        self.kuzu_graph = kuzu_graph
        logger.info("temporal_graph.initialized")

    def _infer_entity_label(self, entity_id: str) -> Optional[str]:
        """Infer the entity label from KuzuDB if it is not explicitly provided."""
        if not self.kuzu_graph.conn:
            self.kuzu_graph.initialize()

        try:
            # Query node labels for the entity ID
            query = f"MATCH (n) WHERE n.id = '{_escape_cypher_string(entity_id)}' RETURN labels(n) AS labels"
            results = self.kuzu_graph.query(query)
            if results and len(results) > 0:
                labels = results[0].get("labels")
                if isinstance(labels, list) and labels:
                    return labels[0]
            logger.warning("temporal_graph.label_inference_failed", entity_id=entity_id)
        except Exception as e:
            logger.warning("temporal_graph.label_inference_error", entity_id=entity_id, error=str(e), exc_info=True)
        return None

    async def add_temporal_property(self, entity_label: str, entity_id: str, property_name: str, value: Any, 
                                    valid_from: Optional[datetime] = None, valid_to: Optional[datetime] = None):
        """Adds or updates a property on an entity with temporal validity."""
        if not self.kuzu_graph.conn:
            self.kuzu_graph.initialize()

        # Ensure valid_from and valid_to are in UTC ISO format
        valid_from_iso = (valid_from or datetime.now(timezone.utc)).isoformat()
        valid_to_iso = (valid_to or datetime.max.replace(tzinfo=timezone.utc)).isoformat()

        
        # Fetch existing node properties to merge
        current_node_query = f"MATCH (n:{entity_label}) WHERE n.id = '{_escape_cypher_string(entity_id)}' RETURN n"
        current_node_result = self.kuzu_graph.query(current_node_query)
        
        existing_properties = {}
        if current_node_result:
            node_data = current_node_result[0].get('n', {})
            existing_properties = {k: v for k, v in node_data.items() if not k.startswith('_')}

        # Update properties with the new temporal value
        updated_properties = {
            **existing_properties,
            "id": entity_id, # Ensure ID is present for add_node
            property_name: value,
            f"{property_name}_valid_from": valid_from_iso,
            f"{property_name}_valid_to": valid_to_iso,
        }
        
        self.kuzu_graph.add_node(entity_label, updated_properties)
        logger.info("temporal_graph.temporal_property_added", entity_id=entity_id, property=property_name, value=value, valid_from=valid_from_iso, valid_to=valid_to_iso)

    async def query_at_time(self, entity_id: str, property_name: str, query_time: datetime, entity_label: Optional[str] = None) -> Any:
        """Retrieves the value of a property for an entity that was valid at a specific time."""
        if not entity_label:
            entity_label = self._infer_entity_label(entity_id)
            if not entity_label:
                raise ValueError("entity_label is required for temporal queries and could not be inferred from entity_id.")

        if not self.kuzu_graph.conn:
            self.kuzu_graph.initialize()

        query_time_iso = query_time.isoformat()

        query = f"""MATCH (n:{entity_label}) 
                    WHERE n.id = '{_escape_cypher_string(entity_id)}' 
                    AND n.{property_name}_valid_from <= '{_escape_cypher_string(query_time_iso)}' 
                    AND n.{property_name}_valid_to >= '{_escape_cypher_string(query_time_iso)}' 
                    RETURN n.{property_name} AS {property_name}_value
                 """
        results = self.kuzu_graph.query(query)
        
        if results and len(results) > 0:
            value = results[0].get(f"{property_name}_value")
            logger.info("temporal_graph.query_at_time_success", entity_id=entity_id, property=property_name, query_time=query_time_iso, result=value)
            return value
        logger.info("temporal_graph.query_at_time_no_result", entity_id=entity_id, property=property_name, query_time=query_time_iso)
        return None
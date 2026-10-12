import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import traceback
import structlog
import redis.asyncio as aioredis
import os

from app.memory.cognee_setup import CogneeMemory
from app.memory.graph_setup import KuzuGraph

logger = structlog.get_logger("aether.memory.ingest_worker")

class MemoryIngestWorker:
    """Consumes agent events from a Redis Stream and processes them through CogneeMemory."""

    def __init__(self, redis_url: str = "redis://localhost:6379", stream_name: str = "agent_events_stream", consumer_group: str = "memory_ingest_group"):
        self.redis_url = redis_url
        self.stream_name = stream_name
        self.consumer_group = consumer_group
        self.consumer_name = f"ingest_worker_{uuid.uuid4().hex[:8]}" # Unique consumer name
        self._running = False
        self.redis_client: Optional[aioredis.Redis] = None
        self.cognee_memory: Optional[CogneeMemory] = None

        logger.info("memory_ingest_worker.initialized", consumer_name=self.consumer_name, redis_url=redis_url)

    async def start(self):
        """Connects to Redis, initializes CogneeMemory, and starts consuming events."""
        if self._running:
            logger.info("memory_ingest_worker.already_running")
            return

        logger.info("memory_ingest_worker.starting", consumer_name=self.consumer_name)
        self._running = True
        self.redis_client = aioredis.from_url(self.redis_url)
        
        # Initialize CogneeMemory for this worker instance.
        # Use a shared KuzuDB path for all backend processes by default.
        kuzu_db_path = os.environ.get("KUZU_DB_PATH", "/tmp/aether/kuzu.db")
        self.cognee_memory = CogneeMemory(config={
            "kuzu_db_path": kuzu_db_path,
            "qdrant_host": os.environ.get("QDRANT_HOST", "localhost"),
            "qdrant_port": int(os.environ.get("QDRANT_PORT", 6333)),
            "openai_api_key": os.environ.get("OPENAI_API_KEY"),
            "openai_api_base": os.environ.get("OPENAI_API_BASE"),
        })
        await self.cognee_memory.initialize()

        # Ensure the Redis Stream consumer group exists
        try:
            await self.redis_client.xgroup_create(self.stream_name, self.consumer_group, id="$", mkstream=True)
            logger.info("memory_ingest_worker.consumer_group_created", group=self.consumer_group, stream=self.stream_name)
        except aioredis.exceptions.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                logger.error("memory_ingest_worker.xgroup_create_error", group=self.consumer_group, stream=self.stream_name, error=str(e), exc_info=True)
                raise
            logger.info("memory_ingest_worker.consumer_group_exists", group=self.consumer_group)

        await self._listen_for_events()
        logger.info("memory_ingest_worker.started_listening", consumer_name=self.consumer_name)

    async def stop(self):
        """Stops the worker and closes Redis connection."""
        logger.info("memory_ingest_worker.stopping", consumer_name=self.consumer_name)
        self._running = False
        if self.redis_client:
            await self.redis_client.close()
        logger.info("memory_ingest_worker.stopped", consumer_name=self.consumer_name)

    async def _listen_for_events(self):
        """Continuously listens for new events from the Redis Stream using a consumer group."""
        while self._running:
            try:
                response = await self.redis_client.xreadgroup(
                    self.consumer_group, self.consumer_name, {self.stream_name: ">"}, count=1, block=1000 # Block for 1 second
                )
                
                if response:
                    for stream, messages in response:
                        for message_id, fields in messages:
                            payload_str = fields[b"payload"].decode("utf-8")
                            event_data = json.loads(payload_str)
                            
                            logger.info("memory_ingest_worker.event_received", message_id=message_id.decode(), event_id=event_data.get("event_id"), consumer=self.consumer_name)
                            
                            # Process event in a separate asyncio task to avoid blocking the stream reader
                            asyncio.create_task(self.process_event(event_data, message_id))
                            
            except asyncio.CancelledError:
                logger.info("memory_ingest_worker.listener_cancelled", consumer=self.consumer_name)
                break
            except Exception as e:
                logger.error("memory_ingest_worker.stream_read_error", consumer=self.consumer_name, error=str(e), exc_info=True)
                await asyncio.sleep(5) # Wait before retrying

    async def process_event(self, event: Dict[str, Any], message_id: bytes):
        """Normalizes an event and feeds it into CogneeMemory, acknowledging it upon completion."""
        event_id = event.get("event_id")
        logger.info("memory_ingest_worker.processing_event", event_id=event_id, consumer=self.consumer_name)

        try:
            if self.cognee_memory:
                await self.cognee_memory.ingest(event)
            else:
                logger.error("memory_ingest_worker.cognee_memory_not_initialized", event_id=event_id)
                return # Do not acknowledge if memory not ready
            logger.info("memory_ingest_worker.event_processed", event_id=event_id, consumer=self.consumer_name)
        except Exception as exc:
            logger.error("memory_ingest_worker.event_processing_failed", event_id=event_id, error=str(exc), exc_info=True, consumer=self.consumer_name)
        finally:
            # Acknowledge the message regardless of success or failure
            if self.redis_client:
                await self.redis_client.xack(self.stream_name, self.consumer_group, message_id)
                logger.info("memory_ingest_worker.event_acknowledged", message_id=message_id.decode(), event_id=event_id, consumer=self.consumer_name)


async def run_worker():
    worker = MemoryIngestWorker()
    try:
        await worker.start()
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.critical("memory_ingest_worker.main_error", error=str(e), exc_info=True)
    finally:
        await worker.stop()

if __name__ == "__main__":
    # Basic setup for structlog in standalone worker
    structlog.configure(
        processors=[
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer()
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.critical("memory_ingest_worker.main_error", error=str(e), exc_info=True)
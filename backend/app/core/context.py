"""
Onsite48 Context Singleton - Single Source of Truth for All Services

This is the foundation for enterprise-grade reliability:
- Single Redis connection (not 4+)
- Consistent tools across all APIs
- Task state survives restarts
- Multi-worker safe
"""

from typing import Optional, Dict, Any
import structlog
import redis.asyncio as aioredis

logger = structlog.get_logger("onsite48.context")


class Onsite48Context:
    """
    The single source of truth for all Onsite48 services.
    
    Usage:
        from app.core.context import onsite48
        
        # In any endpoint
        onsite48.ensure_initialized()
        redis = onsite48.redis_client
        tools = onsite48.tool_registry
    """
    
    _instance: Optional["Onsite48Context"] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if not hasattr(self, '_initialized'):
            self._initialized = False
            self.redis_client: Optional[aioredis.Redis] = None
            self.model_router = None
            self.checkpointer = None
            self.tool_registry = None
            self.mcp_registry = None
            self.domain_router = None
            self.cognee_memory = None
            self.browser_service = None
            self.started_at: Optional[str] = None
    
    @property
    def initialized(self) -> bool:
        return self._initialized
    
    def initialize(self, **kwargs) -> None:
        """Populate the context. Called ONCE from main.py lifespan."""
        if self._initialized:
            logger.warning("context.already_initialized", 
                          message="initialize() called twice - ignoring")
            return
        
        for key, value in kwargs.items():
            setattr(self, key, value)
        
        self._initialized = True
        self.started_at = kwargs.get("started_at")
        
        logger.info("context.initialized", 
                   services=list(kwargs.keys()),
                   started_at=self.started_at)
    
    def ensure_initialized(self) -> None:
        """Raise clear error if context hasn't been initialized."""
        if not self._initialized:
            raise RuntimeError(
                "Onsite48Context not initialized. "
                "This means main.py's lifespan hasn't run. "
                "Check your app startup order."
            )
    
    async def close(self) -> None:
        """Cleanup all resources. Called ONCE on shutdown."""
        if not self._initialized:
            return
        
        logger.info("context.closing")
        
        if self.cognee_memory:
            await self.cognee_memory.close()
        if self.model_router:
            await self.model_router.close()
        if self.redis_client:
            await self.redis_client.close()
        if self.browser_service:
            await self.browser_service.stop()
        
        self._initialized = False
        logger.info("context.closed")


# Global singleton - import this everywhere
onsite48 = Onsite48Context()

__all__ = ["onsite48", "Onsite48Context"]
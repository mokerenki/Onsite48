# backend/app/tools/registry.py

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union

import structlog

from app.core.control_plane_contracts import CapabilityContract, IntentContract, IntentOperation, capability_matches_intent

logger = structlog.get_logger("aether.tools")


def _default_contract_for_tool(name: str) -> tuple[list[str], list[str], str, bool]:
    """Return a conservative typed contract for older built-in schemas.

    New tools should provide these fields directly.  The fallback keeps older
    integrations safe during the migration: it declares the narrowest useful
    resource/operation pair rather than leaving an executable capability
    untyped.
    """
    lowered = name.lower()
    resource = "custom_tool"
    operations = ["execute"]
    exactness = "unknown"
    paginated = False
    if lowered.startswith("computer_"):
        resource, operations = "web_page", ["read", "search", "summarize", "compare", "navigate", "click", "fill"]
    elif lowered in {"browser", "browser_control"}:
        return (
            ["web_page", "general_resource", "research_report"],
            ["read", "search", "summarize", "compare", "navigate", "get", "list"],
            "unknown",
            False,
        )
    elif lowered.startswith("bridge_"):
        resource, operations = "browser_selected_tab", ["read", "search", "list", "summarize", "navigate", "click", "fill"]
    elif lowered in {"current_time", "time"}:
        resource, operations, exactness = "time", ["get"], "exact"
    elif "draft" in lowered:
        resource, operations, exactness = "gmail_draft", ["create"], "exact"
    elif "gmail" in lowered or lowered == "email":
        resource, operations = "gmail_message", ["search"]
    elif "calendar" in lowered:
        resource, operations = "calendar_event", ["list"]
    elif "slack" in lowered:
        resource, operations = "slack_message", ["send"]
    elif "whatsapp" in lowered:
        resource, operations = "whatsapp_message", ["send"]
    elif "chart" in lowered:
        resource, operations, exactness = "chart", ["create"], "exact"
    elif lowered in {"document", "library_search"}:
        resource, operations = ("document", ["create"]) if lowered == "document" else ("library_document", ["search"])
    elif lowered in {"python_repl", "python_execute"}:
        resource, operations = "code", ["execute"]
    elif "research" in lowered:
        resource, operations = "research_report", ["create"]
    elif lowered.startswith("custom_api"):
        resource, operations = "custom_api", ["get"]
    elif lowered.startswith("custom_mcp"):
        resource, operations = "custom_mcp", ["execute"]
    # Browser and computer tools are both public-web runtimes. Declare both
    # "web_page" (for typed browse/resource intents such as compare) and
    # "general_resource" (for legacy general intents) so capability coverage
    # matches in both directions without requiring an additional shortcut.
    if resource == "web_page":
        return ["web_page", "general_resource"], operations, exactness, paginated
    return [resource], operations, exactness, paginated


@dataclass
class ToolSchema:
    name: str
    description: str
    parameters: Dict[str, Any]
    required: List[str] = field(default_factory=list)
    sandbox_template: Optional[str] = None
    irreversible: bool = False
    runtime: Optional[str] = None
    idempotency: Optional[str] = None
    approval_required: Optional[bool] = None
    timeout_seconds: int = 60
    max_attempts: int = 2
    # Additional validation fields
    output_schema: Optional[Dict[str, Any]] = None
    postconditions: List[Dict[str, Any]] = field(default_factory=list)
    domain: str = "general"
    provider: Optional[str] = None
    resources: List[str] = field(default_factory=list)
    operations: List[str] = field(default_factory=list)
    excludes_operations: List[str] = field(default_factory=list)
    exactness: str = "unknown"
    supports_pagination: bool = False
    evidence_schema: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if not self.resources or not self.operations:
            resources, operations, exactness, paginated = _default_contract_for_tool(self.name)
            self.resources = self.resources or resources
            self.operations = self.operations or operations
            if self.exactness == "unknown":
                self.exactness = exactness
            self.supports_pagination = self.supports_pagination or paginated

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": self.parameters,
                "required": self.required,
            },
            "sandbox_template": self.sandbox_template,
            "runtime": self.runtime,
            "idempotency": self.idempotency,
            "approval_required": self.approval_required if self.approval_required is not None else self.irreversible,
            "timeout_seconds": self.timeout_seconds,
            "max_attempts": self.max_attempts,
            "output_schema": self.output_schema,
            "postconditions": self.postconditions,
            "domain": self.domain,
            "provider": self.provider,
            "resources": self.resources,
            "operations": self.operations,
            "excludes_operations": self.excludes_operations,
            "exactness": self.exactness,
            "supports_pagination": self.supports_pagination,
            "evidence_schema": self.evidence_schema or {},
        }

    def capability_contract(self) -> CapabilityContract:
        return CapabilityContract(
            name=self.name,
            domain=self.domain,
            provider=self.provider,
            resource=self.resources[0] if len(self.resources) == 1 else None,
            resources=self.resources,
            operations=[IntentOperation(value) for value in self.operations if value in {item.value for item in IntentOperation}],
            excludes_operations=[IntentOperation(value) for value in self.excludes_operations if value in {item.value for item in IntentOperation}],
            input_schema={"type": "object", "properties": self.parameters, "required": self.required},
            output_schema=self.output_schema or {},
            evidence_schema=self.evidence_schema or {},
            read_only=self.idempotency == "read_only" and not self.irreversible,
            reversible=not self.irreversible,
            irreversible=self.irreversible,
            exactness=self.exactness if self.exactness in {"exact", "estimated", "paginated", "unknown"} else "unknown",
            supports_pagination=self.supports_pagination,
            idempotency=self.idempotency or "unknown",
        )


class BaseTool(ABC):
    def __init__(self):
        self.schema = self._build_schema()

    @abstractmethod
    def _build_schema(self) -> ToolSchema:
        pass

    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        pass

    def validate(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Validate arguments against the tool's schema."""
        # Check required args
        missing = [r for r in self.schema.required if r not in args]
        if missing:
            raise ValueError(f"Missing required arguments: {missing}")
        
        # Additional type validation
        for key, value in args.items():
            param_schema = self.schema.parameters.get(key, {})
            param_type = param_schema.get("type")
            
            if param_type == "string" and not isinstance(value, str):
                raise TypeError(f"Argument '{key}' must be a string, got {type(value).__name__}")
            elif param_type == "integer" and not isinstance(value, int):
                raise TypeError(f"Argument '{key}' must be an integer, got {type(value).__name__}")
            elif param_type == "number" and not isinstance(value, (int, float)):
                raise TypeError(f"Argument '{key}' must be a number, got {type(value).__name__}")
            elif param_type == "boolean" and not isinstance(value, bool):
                raise TypeError(f"Argument '{key}' must be a boolean, got {type(value).__name__}")
            elif param_type == "array" and not isinstance(value, list):
                raise TypeError(f"Argument '{key}' must be an array, got {type(value).__name__}")
            elif param_type == "object" and not isinstance(value, dict):
                raise TypeError(f"Argument '{key}' must be an object, got {type(value).__name__}")
        
        # Additional validation for enum fields
        for key, value in args.items():
            param_schema = self.schema.parameters.get(key, {})
            enum_values = param_schema.get("enum")
            if enum_values is not None and value not in enum_values:
                raise ValueError(f"Argument '{key}' must be one of {enum_values}, got '{value}'")
        
        return args

    def validate_output(self, output: Any) -> bool:
        """Validate output against the tool's output schema if defined."""
        if not self.schema.output_schema:
            return True
        
        # Basic output validation
        if not isinstance(output, dict):
            return False
        
        output_schema = self.schema.output_schema
        required_fields = output_schema.get("required", [])
        for field in required_fields:
            if field not in output:
                return False
        
        return True


class ToolRegistry:
    """Thread-safe tool registry with duplicate prevention and source tracking."""
    
    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}
        self._tool_sources: Dict[str, str] = {}  # Track registration source
        self._lock = None  # Add asyncio.Lock if needed
        logger.info("tool_registry.initialized")

    def register(self, tool: BaseTool, *, source: str = "builtin", allow_overwrite: bool = False) -> "ToolRegistry":
        """
        Register a tool with the registry.
        
        Args:
            tool: The tool to register
            source: Source identifier (e.g., "builtin", "mcp:github")
            allow_overwrite: If True, replace an existing tool deliberately.

        A duplicate registration without an explicit overwrite keeps the first
        implementation. This prevents an accidental shadowing of a trusted
        built-in tool without making startup depend on registration ordering.
        """
        name = tool.schema.name
        if not tool.schema.resources or not tool.schema.operations or not tool.schema.exactness:
            raise ValueError(f"Tool '{name}' must declare resources, operations, and exactness before registration")

        if name in self._tools and not allow_overwrite:
            existing_source = self._tool_sources.get(name, "unknown")
            logger.warning(
                "tool_registry.duplicate_ignored",
                tool=name,
                source=source,
                existing_source=existing_source,
            )
            return self

        if name in self._tools:
            logger.warning("tool_registry.overwrite", tool=name, source=source, old_source=self._tool_sources.get(name))
        
        self._tools[name] = tool
        self._tool_sources[name] = source
        logger.info("tool_registry.registered", tool=name, source=source)
        return self

    def get(self, name: str) -> Optional[BaseTool]:
        """Get a tool by name."""
        return self._tools.get(name)

    def list_tools(self) -> List[str]:
        """List all registered tool names."""
        return list(self._tools.keys())

    def describe_all(self) -> List[Dict[str, Any]]:
        """Get schemas for all registered tools."""
        return [tool.schema.to_dict() for tool in self._tools.values()]

    def unregister(self, name: str) -> None:
        """Unregister a tool."""
        if name in self._tools:
            del self._tools[name]
            self._tool_sources.pop(name, None)
            logger.info("tool_registry.unregistered", tool=name)

    def get_source(self, name: str) -> Optional[str]:
        """Get the source of a registered tool."""
        return self._tool_sources.get(name)
    
    def validate_tool_call(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Validate a tool call against the tool's schema."""
        tool = self.get(tool_name)
        if not tool:
            raise ValueError(f"Tool '{tool_name}' not found")
        return tool.validate(args)

    def validate_semantic_match(self, tool_name: str, intent: IntentContract) -> None:
        """Reject a resource/operation mismatch before a tool can execute."""
        tool = self.get(tool_name)
        if tool is None:
            raise ValueError(f"Tool '{tool_name}' not found")
        schema = tool.schema
        if not schema.resources:
            return
        matched, reasons = capability_matches_intent(intent, schema.capability_contract())
        if not matched:
            raise ValueError("; ".join(reasons))
import os

mcp_dir = "/app/app/mcp"
os.makedirs(mcp_dir, exist_ok=True)

files = {}

files["base_mcp.py"] = """from typing import List, Dict, Any
from abc import ABC, abstractmethod

class BaseMCP(ABC):
    @abstractmethod
    def get_tools(self) -> List[Any]: ...
    @abstractmethod
    async def get_context(self, user_id: str, thread_id: str) -> Dict[str, Any]: ...
    @abstractmethod
    def get_domain_prompt(self) -> str: ...
"""

files["__init__.py"] = """from app.mcp.base_mcp import BaseMCP
from app.mcp.sales_mcp import SalesMCP
from app.mcp.marketing_mcp import MarketingMCP
from app.mcp.finance_mcp import FinanceMCP
from app.mcp.executive_mcp import ExecutiveMCP
from app.mcp.product_mcp import ProductMCP
from app.mcp.healthcareadmin_mcp import HealthcareAdminMCP

__all__ = [
    "BaseMCP",
    "SalesMCP",
    "MarketingMCP",
    "FinanceMCP",
    "ExecutiveMCP",
    "ProductMCP",
    "HealthcareAdminMCP",
]
"""

for name, cls, prompt in [
    ("sales_mcp", "SalesMCP", "You are a sales assistant."),
    ("marketing_mcp", "MarketingMCP", "You are a marketing assistant."),
    ("finance_mcp", "FinanceMCP", "You are a finance assistant."),
    ("executive_mcp", "ExecutiveMCP", "You are an executive assistant."),
    ("product_mcp", "ProductMCP", "You are a product assistant."),
    ("healthcareadmin_mcp", "HealthcareAdminMCP", "You are a healthcare admin assistant."),
]:
    files[f"{name}.py"] = f"""from typing import List, Dict, Any
from app.mcp.base_mcp import BaseMCP
from app.memory.cognee_setup import CogneeMemory

class {cls}(BaseMCP):
    def __init__(self, memory: CogneeMemory):
        self.memory = memory

    def get_tools(self) -> List[Any]:
        return []

    async def get_context(self, user_id: str, thread_id: str) -> Dict[str, Any]:
        return {{}}

    def get_domain_prompt(self) -> str:
        return "{prompt}"
"""

for filename, content in files.items():
    path = os.path.join(mcp_dir, filename)
    with open(path, "w") as f:
        f.write(content)
    print(f"wrote {path}")
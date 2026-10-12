from .sales_schema import SALES_SCHEMA
from .marketing_schema import MARKETING_SCHEMA
from .finance_schema import FINANCE_SCHEMA
from .executive_schema import EXECUTIVE_SCHEMA
from .healthcareadmin_schema import HEALTHCARE_ADMIN_SCHEMA
from .product_schema import PRODUCT_SCHEMA


ALL_DOMAIN_SCHEMAS = {
    "sales": SALES_SCHEMA,
    "marketing": MARKETING_SCHEMA,
    "finance": FINANCE_SCHEMA,
    "executive": EXECUTIVE_SCHEMA,
    "product": PRODUCT_SCHEMA,
    "healthcareadmin": HEALTHCARE_ADMIN_SCHEMA
    
}
"""
Finance Domain Schema for Knowledge Graph

Entities: Invoice, Payment, Budget, Expense, FinancialReport, Account
Relationships: BelongsTo, PaidFor, etc.
"""

FINANCE_SCHEMA = {
    "entities": {
        "Invoice": {
            "properties": {
                "id": "STRING",
                "invoice_number": "STRING",
                "customer_id": "STRING",
                "total_amount": "FLOAT",
                "balance_due": "FLOAT",
                "issue_date": "STRING",
                "due_date": "STRING",
                "status": "STRING",  # Draft, Sent, Paid, Overdue, Cancelled
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "Payment": {
            "properties": {
                "id": "STRING",
                "invoice_id": "STRING",
                "amount": "FLOAT",
                "payment_date": "STRING",
                "method": "STRING",  # Credit Card, Bank Transfer, Cash, Other
                "status": "STRING",  # Pending, Completed, Failed
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "Budget": {
            "properties": {
                "id": "STRING",
                "name": "STRING",
                "year": "INTEGER",
                "total_amount": "FLOAT",
                "spent": "FLOAT",
                "remaining": "FLOAT",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "Expense": {
            "properties": {
                "id": "STRING",
                "description": "STRING",
                "amount": "FLOAT",
                "category": "STRING",
                "expense_date": "STRING",
                "budget_id": "STRING",
                "approved_by": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "FinancialReport": {
            "properties": {
                "id": "STRING",
                "type": "STRING",  # P&L, Balance Sheet, Cash Flow
                "period_start": "STRING",
                "period_end": "STRING",
                "generated_date": "STRING",
                "summary": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        }
    },
    "relationships": {
        "BELONGS_TO": {"from": "Invoice", "to": "Account"},
        "PAID_FOR": {"from": "Payment", "to": "Invoice"},
        "HAS_EXPENSE": {"from": "Budget", "to": "Expense"},
        "GENERATES_REPORT": {"from": "Account", "to": "FinancialReport"},
    }
}
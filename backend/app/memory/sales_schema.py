"""
Sales Domain Schema for Knowledge Graph

Entities: Account, Opportunity, Lead, Contact, Activity, PipelineStage, Quote, Order
Relationships: Owns, HasLead, HasOpportunity, AssignedTo, etc.
Temporal: valid_from, valid_to for all entities and relationships
"""

from typing import Dict, Any

SALES_SCHEMA = {
    "entities": {
        "Account": {
            "properties": {
                "id": "STRING",
                "name": "STRING",
                "industry": "STRING",
                "annual_revenue": "FLOAT",
                "billing_city": "STRING",
                "billing_country": "STRING",
                "website": "STRING",
                "phone": "STRING",
                "owner_id": "STRING",
                "created_date": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "Opportunity": {
            "properties": {
                "id": "STRING",
                "name": "STRING",
                "close_date": "STRING",
                "amount": "FLOAT",
                "stage": "STRING",  # Prospecting, Qualification, Proposal, Negotiation, Closed Won, Closed Lost
                "probability": "FLOAT",
                "account_id": "STRING",
                "owner_id": "STRING",
                "expected_revenue": "FLOAT",
                "created_date": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "Lead": {
            "properties": {
                "id": "STRING",
                "name": "STRING",
                "company": "STRING",
                "email": "STRING",
                "phone": "STRING",
                "status": "STRING",  # New, Contacted, Qualified, Unqualified
                "source": "STRING",  # Web, Referral, LinkedIn, etc.
                "score": "FLOAT",    # Lead score (0-100)
                "owner_id": "STRING",
                "created_date": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "Contact": {
            "properties": {
                "id": "STRING",
                "name": "STRING",
                "email": "STRING",
                "phone": "STRING",
                "title": "STRING",
                "account_id": "STRING",
                "owner_id": "STRING",
                "last_activity_date": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "Activity": {
            "properties": {
                "id": "STRING",
                "subject": "STRING",
                "type": "STRING",  # Email, Call, Meeting, Task
                "activity_date": "STRING",
                "status": "STRING",
                "description": "STRING",
                "who_id": "STRING",   # Contact/Lead ID
                "what_id": "STRING",  # Opportunity/Account ID
                "owner_id": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "PipelineStage": {
            "properties": {
                "id": "STRING",
                "name": "STRING",
                "order": "INTEGER",
                "probability": "FLOAT",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "Quote": {
            "properties": {
                "id": "STRING",
                "name": "STRING",
                "opportunity_id": "STRING",
                "total_price": "FLOAT",
                "status": "STRING",  # Draft, Sent, Accepted, Rejected
                "sent_date": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "Order": {
            "properties": {
                "id": "STRING",
                "name": "STRING",
                "opportunity_id": "STRING",
                "total_amount": "FLOAT",
                "status": "STRING",  # Pending, Completed, Cancelled
                "order_date": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        }
    },
    "relationships": {
        "HAS_OPPORTUNITY": {"from": "Account", "to": "Opportunity"},
        "HAS_LEAD": {"from": "Account", "to": "Lead"},
        "HAS_CONTACT": {"from": "Account", "to": "Contact"},
        "ASSIGNED_TO": {"from": "Opportunity", "to": "User", "target_type": "User"},
        "HAS_ACTIVITY": {"from": "Opportunity", "to": "Activity"},
        "HAS_QUOTE": {"from": "Opportunity", "to": "Quote"},
        "HAS_ORDER": {"from": "Opportunity", "to": "Order"},
        "CONTACT_OF": {"from": "Contact", "to": "Account"},
        "LEAD_SOURCE": {"from": "Lead", "to": "Account"},
        "CONTAINS_STAGE": {"from": "PipelineStage", "to": "Opportunity"},
    }
}
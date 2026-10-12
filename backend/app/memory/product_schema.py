"""
Product Domain Schema for Knowledge Graph

Entities: Product, Feature, Roadmap, UserFeedback, Release, MarketSegment, Competitor, Pricing
Relationships: HasFeature, PlannedFor, BelongsTo, CompetesWith, etc.
Temporal: valid_from, valid_to for all entities and relationships
"""

from typing import Dict, Any

PRODUCT_SCHEMA = {
    "entities": {
        "Product": {
            "properties": {
                "id": "STRING",
                "name": "STRING",
                "description": "STRING",
                "category": "STRING",
                "status": "STRING",  # Concept, Development, Beta, Live, Sunset
                "launch_date": "STRING",
                "version": "STRING",
                "product_manager_id": "STRING",
                "team_id": "STRING",
                "url": "STRING",
                "created_date": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "Feature": {
            "properties": {
                "id": "STRING",
                "name": "STRING",
                "description": "STRING",
                "status": "STRING",  # Backlog, Planning, In Development, QA, Released, Deprecated
                "priority": "STRING",  # P0, P1, P2, P3
                "complexity": "STRING",  # Small, Medium, Large, Epic
                "product_id": "STRING",
                "owner_id": "STRING",
                "epic_link": "STRING",
                "created_date": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "Roadmap": {
            "properties": {
                "id": "STRING",
                "name": "STRING",
                "description": "STRING",
                "quarter": "STRING",  # Q1 2025, Q2 2025, etc.
                "year": "INTEGER",
                "status": "STRING",  # Draft, Active, Completed, Archived
                "product_id": "STRING",
                "owner_id": "STRING",
                "created_date": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "UserFeedback": {
            "properties": {
                "id": "STRING",
                "user_id": "STRING",
                "product_id": "STRING",
                "feedback_type": "STRING",  # Feature Request, Bug Report, Suggestion, Praise
                "title": "STRING",
                "description": "STRING",
                "sentiment": "STRING",  # Positive, Neutral, Negative
                "priority_score": "FLOAT",
                "status": "STRING",  # New, Reviewing, Planned, Implemented, Closed
                "created_date": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "Release": {
            "properties": {
                "id": "STRING",
                "name": "STRING",
                "version": "STRING",
                "product_id": "STRING",
                "release_date": "STRING",
                "status": "STRING",  # Planned, In Progress, Shipped, Postponed
                "release_notes": "STRING",
                "features_included": "STRING",
                "bugs_fixed": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "MarketSegment": {
            "properties": {
                "id": "STRING",
                "name": "STRING",
                "description": "STRING",
                "size": "INTEGER",
                "growth_rate": "FLOAT",
                "revenue_potential": "FLOAT",
                "primary_use_case": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "Competitor": {
            "properties": {
                "id": "STRING",
                "name": "STRING",
                "description": "STRING",
                "website": "STRING",
                "market_share": "FLOAT",
                "strengths": "STRING",
                "weaknesses": "STRING",
                "product_id": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "Pricing": {
            "properties": {
                "id": "STRING",
                "product_id": "STRING",
                "tier_name": "STRING",  # Basic, Pro, Enterprise
                "price": "FLOAT",
                "currency": "STRING",
                "billing_period": "STRING",  # Monthly, Annual, One-time
                "features_included": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "ProductMetric": {
            "properties": {
                "id": "STRING",
                "product_id": "STRING",
                "metric_name": "STRING",  # DAU, MAU, Retention, NPS, Churn
                "value": "FLOAT",
                "target": "FLOAT",
                "unit": "STRING",
                "timestamp": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        }
    },
    "relationships": {
        "HAS_FEATURE": {"from": "Product", "to": "Feature"},
        "PLANNED_FOR": {"from": "Feature", "to": "Roadmap"},
        "BELONGS_TO": {"from": "Feature", "to": "Product"},
        "FEEDBACK_ON": {"from": "UserFeedback", "to": "Product"},
        "HAS_RELEASE": {"from": "Product", "to": "Release"},
        "TARGETS_SEGMENT": {"from": "Product", "to": "MarketSegment"},
        "COMPETES_WITH": {"from": "Product", "to": "Competitor"},
        "HAS_PRICING": {"from": "Product", "to": "Pricing"},
        "MEASURES": {"from": "ProductMetric", "to": "Product"},
        "DERIVED_FROM": {"from": "Release", "to": "Feature"}
    }
}
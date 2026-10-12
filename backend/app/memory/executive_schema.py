"""
Executive Domain Schema for Knowledge Graph

Entities: KPI, Dashboard, Goal, Project, Decision, Meeting, ActionItem
Relationships: Measures, Tracks, etc.
"""

EXECUTIVE_SCHEMA = {
    "entities": {
        "KPI": {
            "properties": {
                "id": "STRING",
                "name": "STRING",
                "value": "FLOAT",
                "target": "FLOAT",
                "unit": "STRING",
                "timestamp": "STRING",
                "owner_id": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "Dashboard": {
            "properties": {
                "id": "STRING",
                "name": "STRING",
                "type": "STRING",  # Sales, Marketing, Finance, Executive
                "created_date": "STRING",
                "owner_id": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "Goal": {
            "properties": {
                "id": "STRING",
                "name": "STRING",
                "description": "STRING",
                "target_value": "FLOAT",
                "start_date": "STRING",
                "end_date": "STRING",
                "status": "STRING",  # Not Started, In Progress, Completed, Cancelled
                "owner_id": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "Project": {
            "properties": {
                "id": "STRING",
                "name": "STRING",
                "description": "STRING",
                "start_date": "STRING",
                "end_date": "STRING",
                "status": "STRING",  # Planning, Active, Review, Done
                "priority": "STRING",  # High, Medium, Low
                "owner_id": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "Decision": {
            "properties": {
                "id": "STRING",
                "title": "STRING",
                "description": "STRING",
                "made_by": "STRING",
                "made_date": "STRING",
                "impact": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "Meeting": {
            "properties": {
                "id": "STRING",
                "title": "STRING",
                "date": "STRING",
                "start_time": "STRING",
                "end_time": "STRING",
                "attendees": "STRING",
                "notes": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "ActionItem": {
            "properties": {
                "id": "STRING",
                "description": "STRING",
                "assignee": "STRING",
                "due_date": "STRING",
                "status": "STRING",  # Pending, In Progress, Done
                "meeting_id": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        }
    },
    "relationships": {
        "MEASURES": {"from": "KPI", "to": "Dashboard"},
        "TRACKS": {"from": "Goal", "to": "Project"},
        "REQUIRES_DECISION": {"from": "Project", "to": "Decision"},
        "HAS_ACTION": {"from": "Meeting", "to": "ActionItem"},
        "ALIGNS_WITH": {"from": "Goal", "to": "KPI"},
    }
}
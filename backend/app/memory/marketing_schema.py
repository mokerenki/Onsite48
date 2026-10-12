"""
Marketing Domain Schema for Knowledge Graph

Entities: Campaign, Email, LandingPage, LeadScore, Segment, AdSet, SocialPost
Relationships: TargetSegment, UsesChannel, etc.
"""

MARKETING_SCHEMA = {
    "entities": {
        "Campaign": {
            "properties": {
                "id": "STRING",
                "name": "STRING",
                "type": "STRING",  # Email, Social, Paid, Content, Event
                "start_date": "STRING",
                "end_date": "STRING",
                "budget": "FLOAT",
                "spent": "FLOAT",
                "status": "STRING",  # Draft, Active, Paused, Completed
                "owner_id": "STRING",
                "created_date": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "Email": {
            "properties": {
                "id": "STRING",
                "subject": "STRING",
                "body": "STRING",
                "sent_date": "STRING",
                "open_rate": "FLOAT",
                "click_rate": "FLOAT",
                "campaign_id": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "LandingPage": {
            "properties": {
                "id": "STRING",
                "url": "STRING",
                "title": "STRING",
                "conversion_rate": "FLOAT",
                "visitor_count": "INTEGER",
                "campaign_id": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "LeadScore": {
            "properties": {
                "id": "STRING",
                "lead_id": "STRING",
                "score": "FLOAT",
                "criteria": "STRING",
                "calculated_date": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "Segment": {
            "properties": {
                "id": "STRING",
                "name": "STRING",
                "criteria": "STRING",
                "size": "INTEGER",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        },
        "SocialPost": {
            "properties": {
                "id": "STRING",
                "platform": "STRING",  # LinkedIn, Twitter, Facebook, Instagram
                "content": "STRING",
                "published_date": "STRING",
                "engagement_count": "INTEGER",
                "campaign_id": "STRING",
                "valid_from": "STRING",
                "valid_to": "STRING"
            }
        }
    },
    "relationships": {
        "TARGETS_SEGMENT": {"from": "Campaign", "to": "Segment"},
        "USES_CHANNEL": {"from": "Campaign", "to": "Email"},
        "DRIVES_TRAFFIC": {"from": "Campaign", "to": "LandingPage"},
        "GENERATES_LEAD": {"from": "Campaign", "to": "Lead"},
        "SCORES_LEAD": {"from": "LeadScore", "to": "Lead"},
        "POSTS_CONTENT": {"from": "Campaign", "to": "SocialPost"},
    }
}
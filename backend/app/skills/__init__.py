"""Skills: on-demand instruction packages (data, not code branches).

Precedence (highest first):
  1. user-level:  $ONSITE48_USER_SKILLS/<user_id>/<name>/
  2. org-level:   $ONSITE48_ORG_SKILLS/<org_id>/<name>/
  3. built-in:    backend/app/skills/<name>/

Skills cannot grant tool permissions; policy still decides.
"""
from app.skills.loader import list_skill_summaries, load_skill_body

__all__ = ["list_skill_summaries", "load_skill_body"]

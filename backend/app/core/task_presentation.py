"""Deterministic task presentation metadata used by APIs and new task creation."""

from __future__ import annotations

import re
from typing import Any


_PRESENTATION_RULES: tuple[tuple[str, str, str, str], ...] = (
    # Very specific categories first (single-concept)
    ("image", "camera", "Image", r"\b(image|photo|picture|visual|graphic|illustration|png|jpg|jpeg|gif|svg|screenshot)\b"),
    ("video", "video", "Video", r"\b(video|footage|clip|stream|recording|movie)\b"),
    ("audio", "audio_lines", "Audio", r"\b(audio|sound|podcast)\b"),
    ("voice", "mic", "Voice", r"\b(voice|speech|dictate|transcribe|record audio)\b"),
    ("music", "music_2", "Music", r"\b(music|song|playlist|melody|beat|track)\b"),
    # Browser automation
    ("click", "mouse_pointer_click", "Click", r"\b(click|press|tap|select button)\b"),
    ("active_tab", "panel_top", "Active Tab", r"\b(active tab|switch tab|focus tab|new tab)\b"),
    ("paired_device", "laptop", "Paired Device", r"\b(device|pair|connect|hardware|peripheral)\b"),
    # Development (specific tools/activities)
    ("repository", "git_branch", "Repository", r"\b(repository|repo|git|branch|pull request|commit|merge)\b"),
    ("bug", "bug", "Bug", r"\b(bug|error|fail|crash|broken|defect|fix|patch)\b"),
    ("testing", "test_tube_2", "Testing", r"\b(test|assert|verify|validation|qa|unit|integration)\b"),
    ("deployment", "rocket", "Deployment", r"\b(deploy|release|version|pipeline|production|staging)\b"),
    ("api", "webhook", "API", r"\b(api|endpoint|rest|graphql|webhook)\b"),
    ("development", "code", "Development", r"\b(code|github|script|program|function|class|method|compile|build)\b"),
    # Documents & data
    ("writing", "pen_line", "Writing", r"\b(write|writing|compose|author|blog|article|essay)\b"),
    ("editing", "file_edit", "Editing", r"\b(edit|revise|redraft|proofread|polish)\b"),
    ("documents", "file", "Documents", r"\b(document|proposal|brief|presentation|slides|memo)\b"),
    ("report", "file_bar_chart_2", "Report", r"\breport\b|\bfindings\b|\bfindings\b|\bdashboard\b"),
    ("spreadsheet", "file_spreadsheet", "Spreadsheet", r"\bspreadsheet|excel|sheet\b|\bformula\b"),
    ("data", "database", "Data", r"\bdata\b|\bdataset\b|\btable\b|\bmetric\b|\bstatistic\b|\bvisualization\b"),
    # Browser/website
    ("browser", "globe", "Browser", r"\bbrowser\b|\bwebsite\b|\bweb page\b|\bwebpage\b|\burl\b|\blinkedin\b|\byoutube\b|\bsite\b|\bnavigate\b|\bfill\b|\bscroll\b|\binspect\b|\bcapture\b|\bobserve\b"),
    ("screenshot", "camera", "Screenshot", r"\bscreenshot\b|\bcapture\b|\brecord\b"),
    # Communication
    ("chat", "message_circle", "Chat", r"\bchat\b|\bconversation\b|\bdm\b|\bdirect message\b"),
    ("support", "life_buoy", "Support", r"\bsupport\b|\btroubleshoot\b|\bservice\b"),
    ("communication", "mail", "Communication", r"\bemail\b|\bgmail\b|\binbox\b|\bdraft\b|\bmessage\b|\breply\b|\boutreach\b|\bcompose\b|\bsend\b"),
    ("news", "newspaper", "News", r"\bnews\b|\bheadline\b|\bupdate\b|\bannouncement\b"),
    ("inbox", "inbox", "Inbox", r"\binbox\b|\bmailbox\b|\breceived\b"),
    # Research & knowledge
    ("research", "search", "Research", r"\bresearch\b|\binvestigate\b|\bdiscover\b|\bstudy\b|\bsurvey\b"),
    ("search", "search_check", "Search", r"\bsearch\b|\bfind\b|\blookup\b|\bquery\b|\bretrieve\b"),
    ("knowledge", "book_open", "Knowledge", r"\bknowledge\b|\btutorial\b|\bguide\b|\bdocumentation\b|\bwiki\b|\breference\b"),
    ("investigation", "compass", "Investigation", r"\bexamine\b|\bprobe\b|\baudit\b"),
    # Design & creative
    ("design", "palette", "Design", r"\bdesign\b|\bui\b|\bux\b|\blayout\b|\bcolor\b|\bfont\b|\btheme\b|\bbrand\b|\bstyle\b|\bmockup\b"),
    # Marketing & sales
    ("funnel", "filter", "Funnel", r"\bfunnel\b|\bconversion\b|\boptimize\b|\blanding page\b|\ba/b\b|\bab test\b"),
    ("marketing", "megaphone", "Marketing", r"\bmarketing\b|\bcampaign\b|\bbehavior\b|\bbrand\b|\bseo\b|\bsocial media\b|\badvertising\b"),
    ("analytics", "chart_no_axes_combined", "Analytics", r"\banalytics\b|\binsight\b|\btrend\b|\bforecast\b|\bprediction\b"),
    ("leads", "contact_round", "Leads", r"\blead\b|\bpipeline\b|\bopportunity\b|\bcrm\b|\bprospect\b|\bdeal\b|\bclosing\b|\bsalesforce\b"),
    # Finance & legal
    ("finance", "dollar_sign", "Finance", r"\bfinance\b|\bfinancial\b|\bbudget\b|\bexpense\b|\bstock\b|\binvestment\b|\bprofit\b|\bcost\b|\btax\b|\bpayment\b|\bsalary\b"),
    ("invoice", "receipt_text", "Invoice", r"\binvoice\b|\bbill\b|\breceipt\b"),
    ("legal", "gavel", "Legal", r"\blegal\b|\bcontract\b|\bpolicy\b|\bcompliance\b|\bregulation\b|\bterms\b|\bprivacy\b"),
    # Automation & scheduling
    ("automation", "zap", "Automation", r"\bautomate\b|\bautomation\b|\btrigger\b|\broutine\b|\bcron\b"),
    ("workflow", "workflow", "Workflow", r"\bworkflow\b|\bprocess\b|\bpipeline\b|\bstage\b|\bstep\b"),
    ("recurring", "repeat_2", "Recurring", r"\bperiodic\b|\bweekly\b|\bdaily\b|\bmonthly\b|\brepeat\b"),
    ("calendar", "calendar", "Calendar", r"\bcalendar\b|\bevent\b|\bappointment\b"),
    ("schedule", "calendar_clock", "Schedule", r"\bschedule\b|\btimeline\b|\bdeadline\b|\bmilestone\b|\btime\b"),
    # People & work
    ("people", "contact_round", "People", r"\bpeople\b|\bperson\b|\buser\b|\bcustomer\b|\bclient\b|\bcontact\b|\bteam\b|\bcolleague\b|\bstaff\b"),
    ("work", "wrench", "Work", r"\bwork\b|\bjob\b|\bproject\b|\bassignment\b|\bchore\b"),
    # Shopping, travel, health, education
    ("shopping", "shopping_bag", "Shopping", r"\bshop\b|\bpurchase\b|\bbuy\b|\bcart\b|\bcheckout\b|\border\b|\bproduct\b|\bitem\b"),
    ("travel", "plane", "Travel", r"\btravel\b|\bflight\b|\bhotel\b|\btrip\b|\bbooking\b"),
    ("health", "heart_pulse", "Health", r"\bhealth\b|\bmedical\b|\bpatient\b|\bdoctor\b|\bfitness\b|\bwellness\b"),
    ("education", "graduation_cap", "Education", r"\beducation\b|\bschool\b|\buniversity\b|\bcourse\b|\bclass\b|\bdegree\b"),
    # Home & security
    ("home", "home", "Home", r"\bhome\b|\bhouse\b|\broom\b|\bkitchen\b|\bliving\b"),
    ("security", "shield_check", "Security", r"\bsecurity\b|\bpassword\b|\bprivacy\b|\bprotection\b|\bencrypt\b|\bauth\b|\bvulnerability\b"),
    ("approval_required", "shield_alert", "Approval", r"\bapproval\b|\bapprove\b|\breview\b|\baccept\b|\breject\b|\bauthorize\b"),
    # Utilities
    ("upload", "cloud_upload", "Upload", r"\bupload\b|\battach\b|\bimport\b"),
    ("download", "cloud_download", "Download", r"\bdownload\b|\bexport\b|\bretrieve\b"),
    # Router domain mappings (for domain-based matching)
    ("product", "check_circle_2", "Product", r"\bproduct\b|\bbacklog\b|\bfeature\b|\brelease\b|\bsprint\b"),
    ("executive", "chart_no_axes_column", "Executive", r"\bkpi\b|\bokr\b|\bstrategy\b|\bboard\b|\bleadership\b"),
    ("healthcare", "heart_pulse", "Healthcare", r"\bpatient\b|\bclaim\b|\binsurance\b|\bmedical\b"),
)

_FILLER_RE = re.compile(
    r"^(?:please\s+|can you\s+|could you\s+|i want you to\s+|go ahead and\s+|help me\s+|i need you to\s+)+",
    re.IGNORECASE,
)


def _clean_prompt(prompt: str) -> str:
    text = re.sub(r"\s+", " ", str(prompt or "")).strip()
    text = _FILLER_RE.sub("", text).strip(" .,:;!?\n\t")
    return text


def task_presentation(prompt: str, domain: str | None = None) -> dict[str, str]:
    """Return stable semantic metadata; never calls a model or external service."""
    text = _clean_prompt(prompt)
    lowered = text.lower()
    normalized_domain = str(domain or "").lower()
    for category, icon_key, label, pattern in _PRESENTATION_RULES:
        if re.search(pattern, lowered) or normalized_domain == category:
            return {"category": category, "icon_key": icon_key, "icon_color": _color_for(category), "title_source": "deterministic"}
    return {"category": "general", "icon_key": "sparkles", "icon_color": "indigo", "title_source": "fallback"}


def _color_for(category: str) -> str:
    return {
        "communication": "blue",
        "development": "emerald",
        "browser": "sky",
        "marketing": "pink",
        "finance": "amber",
        "documents": "slate",
        "data": "cyan",
        "automation": "yellow",
        "research": "violet",
    }.get(category, "indigo")


def concise_task_title(prompt: str, *, category: str | None = None) -> str:
    """Create a useful, bounded title without losing the original prompt."""
    text = _clean_prompt(prompt)
    if not text:
        return "New task"
    text = re.sub(r"https?://\S+", "", text).strip(" .,:;!?-")
    text = re.sub(r"\b(and|then)\s+(report|tell me|show me)\b.*$", "", text, flags=re.IGNORECASE).strip(" .,:;!?-")
    words = text.split()
    if len(words) > 8:
        text = " ".join(words[:8])
    if len(text) > 56:
        text = text[:56].rsplit(" ", 1)[0].rstrip(" .,:;!?-")
    return text[:1].upper() + text[1:]


def ensure_task_presentation(metadata: dict[str, Any] | None, prompt: str, domain: str | None = None) -> dict[str, Any]:
    current = dict(metadata or {})
    presentation = current.get("presentation") if isinstance(current.get("presentation"), dict) else {}
    inferred = task_presentation(prompt, domain)
    current["presentation"] = {**inferred, **presentation}
    return current


def presentation_from_task(task: Any) -> dict[str, str]:
    metadata = task.execution_metadata if isinstance(task.execution_metadata, dict) else {}
    presentation = metadata.get("presentation") if isinstance(metadata.get("presentation"), dict) else {}
    inferred = task_presentation(str(task.prompt or ""), str(task.domain or "general"))
    result = {**inferred, **{key: str(value) for key, value in presentation.items() if value is not None}}
    return result


def title_for_task(task: Any) -> str:
    title = str(getattr(task, "title", "") or "").strip()
    prompt = str(getattr(task, "prompt", "") or "")
    # Legacy records commonly contain the raw prompt as title. Re-title only
    # those records; explicit future user-renamed titles remain untouched.
    if not title or title == prompt or len(title) > 90:
        return concise_task_title(prompt, category=presentation_from_task(task).get("category"))
    return title
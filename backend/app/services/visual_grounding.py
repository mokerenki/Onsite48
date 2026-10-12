"""Vision grounding for the isolated agent computer.

The grounding path is intentionally a narrow fallback: it converts one fresh
viewport screenshot into a validated coordinate proposal. Selector-based
inspection and interaction remain the default because they are faster,
cheaper, and more precise when a reliable selector exists.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

logger = structlog.get_logger("onsite48.services.visual_grounding")


class VisualGroundingService:
    """Locate a visible UI target through the existing Kimi provider route."""

    def __init__(self, model_router: Any):
        self.model_router = model_router

    async def locate(self, *, screenshot_base64: str, description: str, dom_elements: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        target = description.strip()
        if not target or len(target) > 500:
            raise ValueError("A concise visible element description is required for visual grounding.")
        if not screenshot_base64:
            raise ValueError("The current viewport could not be captured for visual grounding.")

        dom_result = self._match_dom(target, dom_elements or [])
        if dom_result is not None:
            return dom_result

        prompt = (
            "Locate the center of the visible interface element described below in the supplied screenshot. "
            "Return strict JSON only, with no markdown or commentary. The JSON schema is: "
            '{"found": boolean, "x": number, "y": number, "confidence": number, "reason": string}. '
            "When found is true, x and y must be normalized viewport coordinates from 0.0 to 1.0, "
            "confidence must be 0.0 to 1.0, and reason must be at most 180 characters. "
            "When the target is not visible or cannot be located reliably, return "
            '{"found": false, "confidence": 0.0, "reason": "short reason"}. '
            f"Target: {target}\n"
            "If DOM references are unavailable, use screenshot coordinates as the fallback."
        )
        # Recovery: a malformed or unavailable vision response is a degraded
        # path, not a failed task. Previously this raised, so one bad JSON
        # response from the model failed the whole locate even when DOM
        # evidence was available and sufficient.
        try:
            response = await self.model_router.route_vision(
                prompt=prompt,
                screenshot_base64=screenshot_base64,
                max_tokens=240,
            )
            return self._validate(response.content)
        except Exception as exc:
            logger.warning(
                "visual_grounding.vision_failed",
                description=target[:120],
                error=str(exc)[:300],
            )
            return {
                "found": False,
                "source": "vision",
                "confidence": 0.0,
                "reason": f"Visual grounding was unavailable: {str(exc)[:140]}",
            }

    @staticmethod
    def _dom_candidates(target: str, dom_elements: list[dict[str, Any]]) -> list[tuple[float, dict[str, Any], str]]:
        """Rank visible DOM elements against the description.

        Returns (rank, element, label) for each usable match, best first. Rank
        separates an exact label from a prefix from a loose substring so the
        best candidate is not simply the first one the page happens to render.
        """
        needle = target.casefold()
        ranked: list[tuple[float, dict[str, Any], str]] = []
        for element in dom_elements or []:
            if not isinstance(element, dict):
                continue
            label = str(element.get("text") or element.get("aria_label") or "").strip()
            selector = element.get("selector")
            bbox = element.get("bbox")
            if not needle or not label or not isinstance(selector, str) or not isinstance(bbox, dict):
                continue
            folded = label.casefold()
            if folded == needle:
                rank = 3.0
            elif folded.startswith(needle):
                rank = 2.0
            elif needle in folded:
                rank = 1.0
            else:
                continue
            x, y = bbox.get("x"), bbox.get("y")
            width, height = bbox.get("width"), bbox.get("height")
            if not all(isinstance(value, (int, float)) for value in (x, y, width, height)):
                continue
            if width <= 0 or height <= 0:
                continue
            ranked.append((rank + len(needle) / 1000.0, element, label))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return ranked

    def _match_dom(self, target: str, dom_elements: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Prefer a DOM match, but never resolve genuine ambiguity by guessing.

        Two controls called "Send" is a coin flip, and this path feeds a click.
        The previous first-match-wins loop returned one of them at a hardcoded
        0.98 confidence, which is how a grounding result becomes confidently
        wrong.
        """
        ranked = self._dom_candidates(target, dom_elements)
        if not ranked:
            return None

        best_rank = ranked[0][0]
        # Same rank to the precision we actually rank on, so "Send" and
        # "Send " are treated as the same label and a longer distinct match wins.
        tied = [item for item in ranked if abs(item[0] - best_rank) < 0.0005]

        def _point(element: dict[str, Any]) -> dict[str, Any]:
            bbox = element["bbox"]
            return {
                "selector": str(element.get("selector"))[:500],
                "dom_ref": element.get("dom_ref"),
                "x": float(bbox["x"] + bbox["width"] / 2),
                "y": float(bbox["y"] + bbox["height"] / 2),
            }

        if len(tied) == 1:
            _, element, label = tied[0]
            return {
                "found": True,
                "source": "dom_ref",
                "ambiguous": False,
                **_point(element),
                "confidence": 0.98,
                "reason": f"Matched the visible element labelled '{label[:120]}'.",
            }

        # Ambiguous: report every candidate and a confidence that reflects the
        # real chance of having picked the right one.
        candidates = [
            {"label": label[:120], **_point(element)} for _, element, label in tied[:5]
        ]
        base = 0.9 if best_rank >= 2.0 else 0.75
        return {
            "found": True,
            "source": "dom_ref",
            "ambiguous": True,
            "candidates": candidates,
            **_point(tied[0][1]),
            "confidence": round(base / len(tied), 3),
            "reason": (
                f"{len(tied)} visible elements match '{target[:80]}'. "
                "Disambiguate before acting on this."
            ),
        }

    @staticmethod
    def _validate(content: str) -> dict[str, Any]:
        """Accept only a JSON object that satisfies the coordinate contract."""
        try:
            payload = json.loads(content.strip())
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("The vision model did not return the required structured coordinate result.") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("found"), bool):
            raise ValueError("The vision model returned an invalid grounding result.")

        reason = str(payload.get("reason", "")).strip()[:180]
        confidence = payload.get("confidence", 0.0)
        if not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
            raise ValueError("The vision model returned an invalid grounding confidence.")

        if not payload["found"]:
            return {
                "found": False,
                "source": "vision",
                "ambiguous": False,
                "confidence": float(confidence),
                "reason": reason or "Target is not visible in the current viewport.",
            }

        x, y = payload.get("x"), payload.get("y")
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            raise ValueError("The vision model returned no usable coordinates.")
        if not 0.0 <= float(x) <= 1.0 or not 0.0 <= float(y) <= 1.0:
            raise ValueError("The vision model returned coordinates outside the viewport.")
        # `source` and `ambiguous` are part of the contract on every path. The
        # vision branch previously omitted `source`, so a consumer branching on
        # it behaved differently depending on which path happened to answer.
        return {
            "found": True,
            "source": "vision",
            "ambiguous": False,
            "x": float(x),
            "y": float(y),
            "confidence": float(confidence),
            "reason": reason or "Located in the current viewport.",
        }
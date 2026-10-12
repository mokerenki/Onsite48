from typing import Any, Dict, List
import structlog
import json
import ast
import operator

logger = structlog.get_logger("aether.heartbeat.policy_engine")

COMPARISON_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}

def _safe_eval_node(node, variables):
    if isinstance(node, ast.Expression):
        return _safe_eval_node(node.body, variables)
    elif isinstance(node, ast.Compare):
        left = _safe_eval_node(node.left, variables)
        right = _safe_eval_node(node.comparators[0], variables)
        op_type = type(node.ops[0])
        if op_type not in COMPARISON_OPS:
            raise ValueError(f"Unsupported operator: {op_type.__name__}")
        return COMPARISON_OPS[op_type](left, right)
    elif isinstance(node, ast.Constant):
        return node.value
    elif isinstance(node, ast.Name):
        if node.id in variables:
            return variables[node.id]
        raise ValueError(f"Unknown variable: {node.id}")
    elif isinstance(node, ast.Attribute):
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        key = ".".join(reversed(parts))
        if key in variables:
            return variables[key]
        raise ValueError(f"Unknown variable: {key}")
    else:
        raise ValueError(f"Unsupported expression: {type(node).__name__}")

def safe_eval_condition(condition_str, variables):
    tree = ast.parse(condition_str, mode='eval')
    return _safe_eval_node(tree, variables)

class PolicyEngine:
    """Evaluates YAML-based rules against probe results to determine severity and actions."""

    def __init__(self):
        self.rules = []
        logger.info("policy_engine.initialized")

    def load_rules(self, rules_config: List[Dict[str, Any]]):
        """Loads policy rules from a configuration list."""
        self.rules = rules_config
        logger.info("policy_engine.rules_loaded", count=len(self.rules))

    def evaluate(self, probe_results: Dict[str, Any]) -> Dict[str, Any]:
        """Matches probe results against rules, returns decisions."""
        evaluated_decision = {"severity": "ok", "action": None, "matched_rule": None}
        
        # Flatten probe results for easier evaluation (e.g., probes.court_roll_rss.status)
        flat_results = {}
        for probe_name, result in probe_results.items():
            for key, value in result.items():
                flat_results[f"probes.{probe_name}.{key}"] = value
        
        # Sort rules by priority if needed (e.g., critical first)
        # For now, process in order of definition
        
        for rule in self.rules:
            condition_str = rule.get("condition")
            action = rule.get("action")
            priority = rule.get("priority", "low")
            rule_name = rule.get("name", "unnamed_rule")

            if not condition_str:
                logger.warning("policy_engine.rule_missing_condition", rule_name=rule_name)
                continue

            try:
                condition_met = safe_eval_condition(condition_str, flat_results)

                if condition_met:
                    evaluated_decision["severity"] = priority
                    evaluated_decision["action"] = action
                    evaluated_decision["matched_rule"] = rule_name
                    logger.info("policy_engine.rule_matched", rule_name=rule_name, condition=condition_str, flat_results=flat_results)
                    # For simplicity, stop on first match. More complex logic might aggregate.
                    break
            except Exception as e:
                logger.error("policy_engine.rule_evaluation_error", rule_name=rule_name, condition=condition_str, error=str(e))

        return evaluated_decision
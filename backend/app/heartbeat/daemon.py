import asyncio
import os
from typing import Any, Dict, List, Optional
import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, time as dt_time, timezone
import structlog

from app.heartbeat.probes import HTTPProbe, FileProbe, ProcessProbe, QueueProbe, BaseProbe
from app.heartbeat.policy_engine import PolicyEngine
from app.services.proactive_worker import create_proactive_task_to_queue
from app.core.context import onsite48

logger = structlog.get_logger("aether.heartbeat.daemon")

class HeartbeatDaemon:
    """Implements the 5-stage heartbeat pipeline: Scheduler → Deterministic Probes → Policy Engine → Escalation Gate → Distributed Action Dispatcher."""

    def __init__(self, config_path: str = None):
        if config_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(base_dir, "heartbeat", "config.yaml")
        self.config_path = config_path
        self._running = False
        self.scheduler = AsyncIOScheduler(timezone="UTC") # Always use UTC for internal scheduling
        self.probes: Dict[str, BaseProbe] = {}
        self.policy_engine = PolicyEngine()
        self.config: Dict[str, Any] = {}
        self.last_probe_run: Dict[str, datetime] = {}

    async def start(self):
        logger.info("heartbeat.daemon.starting")
        self._running = True
        await self.load_config()
        self.initialize_probes()
        self.schedule_probes()
        self.scheduler.start()
        logger.info("heartbeat.daemon.started")

    async def stop(self):
        logger.info("heartbeat.daemon.stopping")
        self._running = False
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False) # Don't wait for jobs to complete on shutdown
        logger.info("heartbeat.daemon.stopped")

    async def load_config(self):
        """Loads and reloads configuration from the YAML file."""
        try:
            with open(self.config_path, 'r') as f:
                new_config = yaml.safe_load(f)
            self.config = new_config
            self.config["last_loaded_at"] = datetime.now(timezone.utc).isoformat()
            self.policy_engine.load_rules(self.config.get("policy_rules", []))
            logger.info("heartbeat.daemon.config_loaded", config_path=self.config_path)
        except FileNotFoundError:
            logger.error("heartbeat.daemon.config_not_found", config_path=self.config_path)
            self.config = {}
        except yaml.YAMLError as e:
            logger.error("heartbeat.daemon.config_parse_error", config_path=self.config_path, error=str(e))
            self.config = {}
        
        # Re-initialize probes and reschedule if config changes (advanced: compare config hashes)
        self.initialize_probes()
        self.reschedule_probes()

    def initialize_probes(self):
        """Initializes probe instances based on current configuration."""
        new_probes = {}
        for probe_name, probe_cfg in self.config.get("probes", {}).items():
            probe_type = probe_cfg.get("type")
            try:
                if probe_type == "http":
                    new_probes[probe_name] = HTTPProbe(probe_cfg)
                elif probe_type == "file":
                    new_probes[probe_name] = FileProbe(probe_cfg)
                elif probe_type == "process":
                    new_probes[probe_name] = ProcessProbe(probe_cfg)
                elif probe_type == "queue":
                    new_probes[probe_name] = QueueProbe(probe_cfg)
                else:
                    logger.warning("heartbeat.daemon.unknown_probe_type", probe_name=probe_name, probe_type=probe_type)
            except Exception as e:
                logger.error("heartbeat.daemon.probe_init_error", probe_name=probe_name, error=str(e))
        self.probes = new_probes
        logger.info("heartbeat.daemon.probes_initialized", count=len(self.probes))

    def reschedule_probes(self):
        """Clears existing jobs and reschedules based on current config."""
        for job in self.scheduler.get_jobs():
            job.remove()
        self.schedule_probes()
        logger.info("heartbeat.daemon.probes_rescheduled")

    def schedule_probes(self):
        """Adds jobs to the scheduler for each configured probe."""
        scheduler_cfg = self.config.get("scheduler", {})
        interval_seconds = scheduler_cfg.get("interval_seconds", 300)
        active_hours_start_str = scheduler_cfg.get("active_hours", {}).get("start", "00:00")
        active_hours_end_str = scheduler_cfg.get("active_hours", {}).get("end", "23:59")
        
        try:
            start_hour, start_minute = map(int, active_hours_start_str.split(':'))
            end_hour, end_minute = map(int, active_hours_end_str.split(':'))
            active_start_time = dt_time(start_hour, start_minute)
            active_end_time = dt_time(end_hour, end_minute)
        except ValueError:
            logger.error("heartbeat.daemon.invalid_active_hours_format", start=active_hours_start_str, end=active_hours_end_str)
            active_start_time = dt_time(0, 0)
            active_end_time = dt_time(23, 59)

        for probe_name in self.probes.keys():
            # Schedule job to run only within active hours
            self.scheduler.add_job(
                self._run_and_evaluate_probe,
                'interval',
                seconds=interval_seconds,
                args=[probe_name],
                start_date=datetime.now(timezone.utc).replace(hour=active_start_time.hour, minute=active_start_time.minute, second=0, microsecond=0),
                end_date=datetime.now(timezone.utc).replace(hour=active_end_time.hour, minute=active_end_time.minute, second=0, microsecond=0),
                jitter=60, # Add jitter to prevent thundering herd problem if many probes are scheduled at the same time
                id=f"probe_{probe_name}"
            )
            logger.info("heartbeat.daemon.probe_scheduled", probe_name=probe_name, interval=interval_seconds, active_hours=f"{active_start_time}-{active_end_time}")

    async def _run_and_evaluate_probe(self, probe_name: str):
        """Executes a probe, evaluates its result with the policy engine, and dispatches actions."""
        if not self._running: return

        logger.debug("heartbeat.daemon.running_probe", probe_name=probe_name)
        probe = self.probes.get(probe_name)
        if not probe:
            logger.error("heartbeat.daemon.probe_not_found", probe_name=probe_name)
            return

        try:
            probe_result = await probe.check()
            self.last_probe_run[probe_name] = datetime.now(timezone.utc)
            logger.debug("heartbeat.daemon.probe_result", probe_name=probe_name, result=probe_result)

            # Policy Evaluation (securely implemented)
            policy_decision = self.policy_engine.evaluate({probe_name: probe_result})
            logger.info("heartbeat.daemon.policy_decision", probe_name=probe_name, decision=policy_decision)

            # Escalation Gate & Distributed Action Dispatcher
            await self._handle_escalation_and_dispatch(probe_name, probe_result, policy_decision)

        except Exception as e:
            logger.error("heartbeat.daemon.probe_error", probe_name=probe_name, error=str(e), exc_info=True)

    async def _handle_escalation_and_dispatch(self, probe_name: str, probe_result: Dict[str, Any], policy_decision: Dict[str, Any]):
        """Determines escalation path and dispatches proactive tasks to the queue."""
        severity = policy_decision.get("severity", "ok")
        action = policy_decision.get("action")
        matched_rule = policy_decision.get("matched_rule")

        task_description = f"Heartbeat Alert: Probe `{probe_name}` reported `{severity}`. Matched rule: `{matched_rule}`."
        context = f"Probe Result: {probe_result}\nPolicy Action: {action}"
        
        # Use LLM for analysis/action suggestion only if policy dictates or for critical/warn states
        if severity == "critical":
            # For critical, we might want a specific LLM analysis task
            llm_prompt = f"Critical issue detected by heartbeat probe {probe_name}: {probe_result}. Policy action: {action}. Analyze the situation and suggest immediate steps to mitigate and resolve. Consider the context of legal, procurements, or healthcare documentation systems."
            await create_proactive_task_to_queue(
                task_description=task_description,
                context=f"{context}\nLLM Analysis Request: {llm_prompt}",
                priority="high",
                action_type="critical_alert_analysis"
            )
            logger.critical("heartbeat.daemon.critical_escalation_dispatched", probe_name=probe_name, action=action)

        elif severity == "warn":
            # For warnings, a less urgent LLM analysis or a notification task
            llm_prompt = f"Warning detected by heartbeat probe {probe_name}: {probe_result}. Policy action: {action}. Analyze if further action is needed or if this is a transient issue. Consider the context of legal, procurements, or healthcare documentation systems."
            await create_proactive_task_to_queue(
                task_description=task_description,
                context=f"{context}\nLLM Analysis Request: {llm_prompt}",
                priority="medium",
                action_type="warning_analysis"
            )
            logger.warning("heartbeat.daemon.warn_escalation_dispatched", probe_name=probe_name, action=action)

        elif action:
            # For 'ok' severity with a defined action, dispatch a task to execute that action
            await create_proactive_task_to_queue(
                task_description=f"Heartbeat Action Required: {action} for probe {probe_name}",
                context=context,
                priority="low",
                action_type=action # The action itself becomes the type
            )
            logger.info("heartbeat.daemon.action_dispatched", probe_name=probe_name, action=action)

        else:
            logger.debug("heartbeat.daemon.no_escalation_or_action", probe_name=probe_name, severity=severity, action=action)


# Global instance
heartbeat_daemon = HeartbeatDaemon()
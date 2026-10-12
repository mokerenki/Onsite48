from fastapi import APIRouter, HTTPException, status
from typing import Dict, Any, List
import yaml
import os
import structlog

from app.heartbeat.daemon import heartbeat_daemon
from app.core.context import onsite48

logger = structlog.get_logger("aether.api.heartbeat_config")
router = APIRouter()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, "heartbeat", "config.yaml")


@router.get("/heartbeat/config", response_model=Dict[str, Any])
async def get_heartbeat_config() -> Dict[str, Any]:
    """Retrieves the current heartbeat configuration."""
    try:
        with open(CONFIG_FILE, 'r') as f:
            config = yaml.safe_load(f)
        logger.info("heartbeat_config.get", status="success")
        return config
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Heartbeat configuration file not found")
    except yaml.YAMLError as e:
        logger.error("heartbeat_config.get", status="error", error=str(e), exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error parsing configuration: {str(e)}")
    except Exception as e:
        logger.error("heartbeat_config.get", status="error", error=str(e), exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Unexpected error reading configuration: {str(e)}")


@router.post("/heartbeat/config", response_model=Dict[str, Any])
async def update_heartbeat_config(new_config: Dict[str, Any]) -> Dict[str, Any]:
    """Updates the heartbeat configuration and triggers a reload of the daemon."""
    try:
        # Basic validation: ensure required top-level keys exist
        if not all(key in new_config for key in ["scheduler", "probes", "policy_rules"]):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing required configuration sections (scheduler, probes, policy_rules)")

        # Attempt to dump to YAML to validate structure before writing
        try:
            yaml_output = yaml.safe_dump(new_config, indent=2)
        except yaml.YAMLError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid YAML structure in provided configuration: {str(e)}")

        with open(CONFIG_FILE, 'w') as f:
            f.write(yaml_output)
        
        # Trigger the daemon to reload its configuration and reschedule probes
        await heartbeat_daemon.load_config()

        logger.info("heartbeat_config.update", status="success")
        return {"message": "Heartbeat configuration updated and reloaded successfully", "config": new_config}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error("heartbeat_config.update", status="error", error=str(e), exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error updating configuration: {str(e)}")


@router.get("/heartbeat/status", response_model=Dict[str, Any])
async def get_heartbeat_status() -> Dict[str, Any]:
    """Retrieves the current operational status of the heartbeat daemon and its probes."""
    # This endpoint provides real-time status from the running daemon instance
    status_info = {
        "daemon_running": heartbeat_daemon._running,
        "scheduler_running": heartbeat_daemon.scheduler.running,
        "probes_configured_count": len(heartbeat_daemon.probes),
        "last_probe_runs": {name: ts.isoformat() for name, ts in heartbeat_daemon.last_probe_run.items()},
        "config_last_loaded": heartbeat_daemon.config.get("last_loaded_at", "N/A"),
    }
    logger.info("heartbeat_config.status_retrieved")
    return status_info
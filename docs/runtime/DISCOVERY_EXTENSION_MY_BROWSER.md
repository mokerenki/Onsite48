# Discovery — Extension My Browser (v3.1)

## Current protocol
- `onsite48-bridge.v3.1` / extension 3.1.0
- Task-owned tab group is primary workspace
- Device pair path: `/api/bridge/device/pair/start|complete|refresh`

## Key paths
- Extension: `extension/onsite48-bridge/`
- API: `backend/app/api/browser_bridge.py`
- Session: `backend/app/services/browser_session.py`
- Tools (preferred): `backend/app/runtime/browser/tools.py` (single `browser` tool)
- Legacy bridge tools: `backend/app/tools/browser_bridge_tools.py` (compat; do not advertise to model)

## Invariants
- command_id + lease_token, terminal receipts
- captureTabScoped / capture_unavailable
- No planning in extension

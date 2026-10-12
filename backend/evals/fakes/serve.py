
"""One-command start for all eval fakes."""
from __future__ import annotations
import threading, time, uvicorn

def _run(app_path, port):
    uvicorn.run(app_path, host="127.0.0.1", port=port, log_level="warning", access_log=False)

def main():
    threading.Thread(target=_run, args=("evals.fakes.google.app:app", 8765), daemon=True).start()
    threading.Thread(target=_run, args=("evals.fakes.web.app:app", 8766), daemon=True).start()
    print("Eval fakes: Google :8765  Mini-web :8766")
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        print("Stopped.")

if __name__ == "__main__":
    main()

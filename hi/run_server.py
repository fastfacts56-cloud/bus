"""
Start the production server with Waitress.

start.bat runs this file inside the virtual environment. Waitress is a real
production WSGI server (unlike Flask's development server).
"""

import os
from pathlib import Path

from waitress import serve

from app import app

PID_FILE = Path(__file__).resolve().parent / "instance" / "server.pid"


def main():
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")

    print("=" * 68)
    print("School Bus Tracker")
    print(f"Local address : http://{app.config['HOST']}:{app.config['PORT']}")
    print(f"Public URL    : {app.config['PUBLIC_BASE_URL']}")
    print("Stop the server with stop.bat (or press Ctrl+C in this window).")
    print("=" * 68)

    try:
        # Eight threads is plenty for 20-50 students and 20 buses.
        serve(app, host=app.config["HOST"], port=app.config["PORT"], threads=8)
    finally:
        try:
            PID_FILE.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()

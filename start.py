"""
Run without bash:  .venv/bin/python start.py   (after venv exists)

Or:  python3 start.py  if dependencies are installed globally.
Sets SECRET_KEY automatically when missing.
"""
import os

os.environ.setdefault("SECRET_KEY", "dev-local-change-me")

# Import app after env is set
from app import app  # noqa: E402

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("\n  → http://127.0.0.1:%s\n" % port)
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")

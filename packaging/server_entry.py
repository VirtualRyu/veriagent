"""Console entry point: never imports the GUI or writes diagnostics to stdout."""
import sys
from pathlib import Path
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veriagent.server import main

if __name__ == "__main__":
    main()

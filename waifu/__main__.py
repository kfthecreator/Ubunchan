"""python -m waifu            -> desktop app
python -m waifu --cli      -> terminal chat (Phase 1 REPL)
python -m waifu --check C  -> only run the safety check on command C
"""
import sys

args = sys.argv[1:]
if "--cli" in args or "--check" in args:
    from .cli import main
    sys.exit(main([a for a in args if a != "--cli"]))

from .gui.app import main

sys.exit(main(args))

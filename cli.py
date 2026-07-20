#!/usr/bin/env python3
"""Thin shim so `python cli.py ...` still works from a clone.

The real implementation lives in the package (gemini_openai/cli.py) so it can
ship as the `gemini-web-api-cli` console script for `uvx`.
"""

from gemini_openai.cli import main

if __name__ == "__main__":
    main()

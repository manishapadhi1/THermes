#!/usr/bin/env python3
"""THermes — Agentic Trading Platform launcher."""
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from server import app
import uvicorn

if __name__ == "__main__":
    print("""
    ╔═══════════════════════════════════════════╗
    ║         THermes — Agentic Trading         ║
    ║     Scania Tegel Design | Multi-LLM       ║
    ╚═══════════════════════════════════════════╝
    """)
    uvicorn.run(app, host="127.0.0.1", port=8788, log_level="info")

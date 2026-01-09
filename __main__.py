#!/usr/bin/env python3
"""
Main entry point for running the attendance bot as a module.

Usage: python -m slack_attendance_bot
"""

import sys
import os

# Add project root to Python path
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Now import and run the app
from app import main

if __name__ == "__main__":
    main()
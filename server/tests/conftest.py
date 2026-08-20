import sys
import os

# Add the server/ directory to sys.path so tests can import server modules directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

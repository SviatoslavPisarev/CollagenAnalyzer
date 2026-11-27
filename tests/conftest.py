import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))      # the app folder (collagen_core.py, gelstats.py, ...)
sys.path.insert(0, HERE)                       # synth.py

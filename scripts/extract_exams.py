#!/usr/bin/env python3
"""CLI de extracao de exames.

Uso:
    python scripts/extract_exams.py [--jobs N] [--verbose]

Importa a logica de parsing de src/extract_exams.py e executa.
"""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from extract_exams import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())

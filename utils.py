#!/usr/bin/env python3
"""
Shared utility functions for the Hermes project.
"""
import re


def extract_bank_from_filename(filename: str) -> str:
    """Extract bank name from filename pattern: 'Bank-Company-YYMMDD.pdf'.

    Examples:
        'Goldman Sachs-TSMC-260508.pdf' -> 'Goldman Sachs'
        'Morgan Stanley-NVIDIA（AI）-260507.pdf' -> 'Morgan Stanley'
    """
    m = re.match(r'^([A-Za-z\s&.]+?)[-（(]', filename)
    return m.group(1).strip() if m else "?"

"""Small parsers and column helpers for OSM and Overture attribute values."""

import numpy as np
import pandas as pd
import re
from .model import _LEN_RE


def parse_length(value) -> float:
    """OSM length tag ("12", "12.5 m", "40 ft", "40'6\"") -> metres, NaN when unusable."""
    if value is None:
        return float("nan")
    if isinstance(value, (int, float, np.integer, np.floating)):
        v = float(value)
        return v if np.isfinite(v) and 0 < v < 900 else float("nan")
    m = _LEN_RE.match(str(value).split(";")[0])
    if not m:
        return float("nan")
    v = float(m.group("v").replace(",", "."))
    unit = (m.group("u") or "m").lower()
    if unit in ("ft", "feet", "foot", "'"):
        v *= 0.3048
        if m.group("i"):
            v += float(m.group("i")) * 0.0254
    return v if 0 < v < 900 else float("nan")


def parse_number(value) -> float:
    """First number found in a value such as ``3`` or ``"3;4"``; NaN otherwise."""
    if value is None:
        return float("nan")
    if isinstance(value, (int, float, np.integer, np.floating)):
        v = float(value)
        return v if np.isfinite(v) else float("nan")
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(value))
    return float(m.group(0).replace(",", ".")) if m else float("nan")


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    if name in df.columns:
        return df[name]
    return pd.Series([None] * len(df), index=df.index, dtype=object)


def _first(value):
    """OSMnx sometimes returns lists for merged tags; take the first item."""
    if isinstance(value, (list, tuple, np.ndarray)):
        return value[0] if len(value) else None
    return value


def _is_yes(series: pd.Series) -> np.ndarray:
    """Tag present and not an explicit 'no'."""
    s = series.map(_first)
    return (s.notna() & (s.astype(str).str.lower() != "no")).to_numpy()

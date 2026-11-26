"""Blend several models' hourly values into one series, with per-variable weights (sites/<site>/model_weights.json) or equal weights.
Pure stdlib (the daily job does not install pandas). Directions are blended as angles (a weighted circular mean), everything else linearly.
A model with weight 0 for a variable is never used for it (e.g. GFS gusts). A variable with no entry in the table gets equal weights over the
table's models. A model with no value for an hour is skipped and the rest are re-weighted."""
import json, math
from pathlib import Path


def load(path):
    """The weights table, or None if the file is missing or unreadable (the job then falls back to equal weights)."""
    try: return json.loads(Path(path).read_text())
    except (OSError, ValueError): return None


def weights_for(table, var, models, mode):
    """{model: weight} over `models`. mode 'equal' (or no table) -> equal over all of them. mode 'weighted' -> the table's weights for
    `var` limited to the table's models; a variable without an entry -> equal over the table's models."""
    if mode != "weighted" or not table: return {m: 1.0 for m in models}
    allowed = [m for m in models if m in table["models"]]
    w = table["weights"].get(var)
    if w is None: return {m: 1.0 for m in allowed}
    return {m: float(w.get(m, 0.0)) for m in allowed}


def _blend_one(vals, w, circular):
    pairs = [(v, w[m]) for m, v in vals.items() if v is not None and w.get(m, 0) > 0]
    if not pairs: return None
    tot = sum(x for _, x in pairs)
    if circular:
        s = sum(x * math.sin(math.radians(v)) for v, x in pairs); c = sum(x * math.cos(math.radians(v)) for v, x in pairs)
        return None if abs(s) < 1e-9 and abs(c) < 1e-9 else math.degrees(math.atan2(s, c)) % 360
    return sum(v * x for v, x in pairs) / tot


def blend_series(series, var, table, mode, idx=None):
    """series: {model: list of hourly values}. idx: which hours to blend (default all). Returns the blended list (None where no model has a value)."""
    models = list(series)
    w = weights_for(table, var, models, mode); circ = var.startswith("wind_direction")
    n = max((len(s) for s in series.values()), default=0)
    out = []
    for i in (range(n) if idx is None else idx):
        vals = {m: (series[m][i] if i < len(series[m]) else None) for m in models}
        out.append(_blend_one(vals, w, circ))
    return out

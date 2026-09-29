"""Saturation (pressure-temperature) tables for common residential refrigerants.

Tables are generated once with CoolProp and cached to refrigerant_tables.json.
Blends with temperature glide get separate bubble (liquid) and dew (vapor) curves:
  - superheat uses the DEW point of the suction pressure
  - subcooling uses the BUBBLE point of the liquid pressure
"""
import json, os, bisect

PA_PER_PSI = 6894.757

# name -> (CoolProp fluid string, upper limit in psia to try)
# Mixture strings use MOLE fractions (converted from the standard mass fractions).
FLUIDS = {
    "R-410A": ("R410A", 700),
    "R-32":   ("R32", 790),
    "R-454B": ("R32[0.8292]&R1234yf[0.1708]", 740),                # 68.9 / 31.1 mass %
    "R-22":   ("R22", 700),
    "R-134a": ("R134a", 570),
    "R-407C": ("R32[0.3811]&R125[0.1796]&R134a[0.4393]", 640),     # 23 / 25 / 52 mass %
}

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "refrigerant_tables.json")


def _k_to_f(k):
    return (k - 273.15) * 9 / 5 + 32


def _build(fluid, pmax):
    import CoolProp.CoolProp as CP
    rows, fails = [], 0
    for psia in range(5, pmax + 1):
        pa = psia * PA_PER_PSI
        try:
            tb = CP.PropsSI("T", "P", pa, "Q", 0, fluid)
            td = CP.PropsSI("T", "P", pa, "Q", 1, fluid)
            rows.append([psia, round(_k_to_f(tb), 2), round(_k_to_f(td), 2)])
            fails = 0
        except Exception:
            fails += 1
            if rows and fails > 5:      # reached the critical region
                break
    return rows


class Tables:
    def __init__(self, log=print):
        self.log = log
        self.data = {}
        self.error = None
        self._load()

    def _load(self):
        if os.path.exists(CACHE):
            try:
                with open(CACHE) as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {}
        missing = [n for n in FLUIDS if n not in self.data]
        if not missing:
            return
        try:
            import CoolProp  # noqa: F401
        except ImportError:
            self.error = "CoolProp is not installed, so saturation temperatures are unavailable (pip install CoolProp)."
            self.log("[pt] " + self.error)
            return
        for name in missing:
            fluid, pmax = FLUIDS[name]
            self.log(f"[pt] building {name} table (one-time)...")
            rows = _build(fluid, pmax)
            if len(rows) < 50:
                self.log(f"[pt] {name}: CoolProp could not generate a table, skipping")
                continue
            self.data[name] = rows
        with open(CACHE, "w") as f:
            json.dump(self.data, f)
        self.log(f"[pt] tables ready: {', '.join(self.data)}")

    def available(self):
        return [n for n in FLUIDS if n in self.data]

    def _interp(self, name, psia, col):
        rows = self.data.get(name)
        if not rows or psia is None:
            return None
        ps = [r[0] for r in rows]
        if psia < ps[0] or psia > ps[-1]:
            return None
        i = bisect.bisect_left(ps, psia)
        if i == 0:
            return rows[0][col]
        p0, p1 = rows[i - 1][0], rows[i][0]
        t0, t1 = rows[i - 1][col], rows[i][col]
        return t0 + (t1 - t0) * (psia - p0) / (p1 - p0)

    def bubble_f(self, name, psia):
        return self._interp(name, psia, 1)

    def dew_f(self, name, psia):
        return self._interp(name, psia, 2)

import os
import json
import pandas as pd

# Common locations where the cloned pulse repo might live
_CANDIDATE_PATHS = [
    r"D:\Guvi project\Phone pay\pulse",
    r"D:\phonepe\pulse",
    os.path.join(os.path.expanduser("~"), "pulse"),
    os.path.join(os.getcwd(), "pulse"),
    os.path.join(os.getcwd(), "phonepe_pulse_data"),
]

def _find_pulse_root() -> str:
    """Return the first candidate path that actually contains pulse data."""
    for p in _CANDIDATE_PATHS:
        probe = os.path.join(p, "data", "aggregated", "transaction")
        if os.path.isdir(probe):
            return p
    return _CANDIDATE_PATHS[0]  # fallback

PULSE_ROOT = _find_pulse_root()


class PhonePeDataExtractor:
    def __init__(self, base_path: str = PULSE_ROOT):
        self.base_path = base_path

    @staticmethod
    def _safe_metric(raw) -> dict:
        """
        The PhonePe JSON stores 'metric' in two ways depending on the folder:
          • aggregated / top  →  {'count': N, 'amount': X}       (dict)
          • map               →  [{'count': N, 'amount': X}]     (list)
        This helper normalises both into a plain dict.
        """
        if isinstance(raw, list):
            return raw[0] if raw else {}
        if isinstance(raw, dict):
            return raw
        return {}

    def _iter_json(self, rel_path: str):
        """
        Walk base_path/rel_path/<state>/<year>/<quarter>.json
        and yield (state_slug, year_int, quarter_int, parsed_dict).
        Skips files that cannot be parsed.
        """
        base = os.path.join(self.base_path, rel_path)
        if not os.path.isdir(base):
            print(f"Directory not found: {base}")
            return

        for state in sorted(os.listdir(base)):
            state_path = os.path.join(base, state)
            if not os.path.isdir(state_path):
                continue
            for year_str in sorted(os.listdir(state_path)):
                year_path = os.path.join(state_path, year_str)
                if not os.path.isdir(year_path):
                    continue
                try:
                    year = int(year_str)
                except ValueError:
                    continue
                for qfile in sorted(os.listdir(year_path)):
                    if not qfile.endswith(".json"):
                        continue
                    try:
                        quarter = int(qfile.replace(".json", ""))
                    except ValueError:
                        continue
                    fpath = os.path.join(year_path, qfile)
                    try:
                        with open(fpath, "r", encoding="utf-8") as fh:
                            yield state, year, quarter, json.load(fh)
                    except Exception:
                        pass  # skip corrupt file silently

    @staticmethod
    def _fmt_state(slug: str) -> str:
        """Convert a PhonePe state slug to Title Case matching GeoJSON."""
        return slug.replace("-", " ").title().replace(" & ", " & ")

    def extract_aggregated_transaction(self) -> pd.DataFrame:
        rows = []
        rel = os.path.join("data", "aggregated", "transaction", "country", "india", "state")
        for state, year, quarter, jd in self._iter_json(rel):
            for txn in (jd.get("data") or {}).get("transactionData") or []:
                instruments = txn.get("paymentInstruments") or []
                pi = instruments[0] if instruments else {}
                rows.append({
                    "State":              self._fmt_state(state),
                    "Year":               year,
                    "Quarter":            quarter,
                    "Transaction_Type":   txn.get("name", ""),
                    "Transaction_Count":  int(pi.get("count", 0)),
                    "Transaction_Amount": float(pi.get("amount", 0)),
                })
        return pd.DataFrame(rows)

    def extract_aggregated_user(self) -> pd.DataFrame:
        rows = []
        rel = os.path.join("data", "aggregated", "user", "country", "india", "state")
        for state, year, quarter, jd in self._iter_json(rel):
            for u in (jd.get("data") or {}).get("usersByDevice") or []:
                rows.append({
                    "State":           self._fmt_state(state),
                    "Year":            year,
                    "Quarter":         quarter,
                    "User_Type":       u.get("brand", "Unknown"),
                    "User_Count":      int(u.get("count", 0)),
                    "User_Percentage": float(u.get("percentage", 0)),
                })
        return pd.DataFrame(rows)

    def extract_map_transaction(self) -> pd.DataFrame:
        rows = []
        rel = os.path.join("data", "map", "transaction", "hover", "country", "india", "state")
        for state, year, quarter, jd in self._iter_json(rel):
            for d in (jd.get("data") or {}).get("hoverDataList") or []:
                metric = self._safe_metric(d.get("metric"))
                rows.append({
                    "State":              self._fmt_state(state),
                    "Year":               year,
                    "Quarter":            quarter,
                    "District":           d.get("name", ""),
                    "Transaction_Count":  int(metric.get("count", 0)),
                    "Transaction_Amount": float(metric.get("amount", 0)),
                })
        return pd.DataFrame(rows)

    def extract_map_user(self) -> pd.DataFrame:
        rows = []
        rel = os.path.join("data", "map", "user", "hover", "country", "india", "state")
        for state, year, quarter, jd in self._iter_json(rel):
            hover = (jd.get("data") or {}).get("hoverData") or {}
            if isinstance(hover, dict):
                for district, vals in hover.items():
                    rows.append({
                        "State":            self._fmt_state(state),
                        "Year":             year,
                        "Quarter":          quarter,
                        "District":         district,
                        "Registered_Users": int((vals or {}).get("registeredUsers", 0)),
                        "App_Opens":        int((vals or {}).get("appOpens", 0)),
                    })
        return pd.DataFrame(rows)

    def extract_top_transaction(self) -> pd.DataFrame:
        rows = []
        rel = os.path.join("data", "top", "transaction", "country", "india", "state")
        for state, year, quarter, jd in self._iter_json(rel):
            for p in (jd.get("data") or {}).get("pincodes") or []:
                metric = self._safe_metric(p.get("metric"))
                rows.append({
                    "State":              self._fmt_state(state),
                    "Year":               year,
                    "Quarter":            quarter,
                    "Pincode":            str(p.get("name", p.get("entityName", ""))),
                    "Transaction_Count":  int(metric.get("count", 0)),
                    "Transaction_Amount": float(metric.get("amount", 0)),
                })
        return pd.DataFrame(rows)

    def extract_top_user(self) -> pd.DataFrame:
        rows = []
        rel = os.path.join("data", "top", "user", "country", "india", "state")
        for state, year, quarter, jd in self._iter_json(rel):
            for d in (jd.get("data") or {}).get("districts") or []:
                rows.append({
                    "State":            self._fmt_state(state),
                    "Year":             year,
                    "Quarter":          quarter,
                    "District":         d.get("name", ""),
                    "Registered_Users": int(d.get("registeredUsers", 0)),
                })
        return pd.DataFrame(rows)

    def extract_all(self) -> dict:
        results = {}
        steps = [
            ("aggregated_transaction", self.extract_aggregated_transaction),
            ("aggregated_user",        self.extract_aggregated_user),
            ("map_transaction",        self.extract_map_transaction),
            ("map_user",               self.extract_map_user),
            ("top_transaction",        self.extract_top_transaction),
            ("top_user",               self.extract_top_user),
        ]
        for name, fn in steps:
            print(f"Extracting {name}...")
            try:
                df = fn()
                results[name] = df
                print(f"  -> Success: {len(df):,} rows")
            except Exception as e:
                print(f"  -> Failed: {e}")
                results[name] = pd.DataFrame()
        return results


if __name__ == "__main__":
    print(f"Detected PULSE_ROOT: {PULSE_ROOT}")
    extractor = PhonePeDataExtractor()
    data = extractor.extract_all()
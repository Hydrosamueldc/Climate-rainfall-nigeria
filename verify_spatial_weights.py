"""Verify study-box coverage and cosine weighting against spherical cell areas."""
from pathlib import Path
import json

import numpy as np
import xarray as xr


def main():
    with xr.open_dataset("data/chirps_nigeria.nc") as dataset:
        rain = dataset.precip.where(dataset.precip >= 0)
        latitude = rain.latitude.values
        longitude = rain.longitude.values
        lat_step = float(np.diff(latitude).mean())
        lon_step = float(np.diff(longitude).mean())
        np.testing.assert_allclose(np.diff(latitude), lat_step, atol=1e-4)
        np.testing.assert_allclose(np.diff(longitude), lon_step, atol=1e-4)
        present = rain.notnull()
        valid = present.all("time")
        intermittent = present.any("time") & ~valid
        assert not bool(intermittent.any()), "Coverage changes between months."

        # Spherical cell area is proportional to sin(north edge)-sin(south edge).
        # Longitude width is constant and cancels when weights are normalized.
        latitude64 = rain.latitude.astype("float64")
        cosine = np.cos(np.deg2rad(latitude64))
        spherical = (
            np.sin(np.deg2rad(latitude64 + abs(lat_step) / 2))
            - np.sin(np.deg2rad(latitude64 - abs(lat_step) / 2))
        )
        records = []
        for name, mask in [
            ("study_box", xr.ones_like(valid, dtype=bool)),
            ("north", rain.latitude >= 9),
            ("south", rain.latitude < 9),
        ]:
            available = valid & mask
            normalized_cosine = cosine.where(available, 0)
            normalized_cosine /= normalized_cosine.sum()
            normalized_spherical = spherical.where(available, 0)
            normalized_spherical /= normalized_spherical.sum()
            np.testing.assert_allclose(normalized_cosine, normalized_spherical, atol=1e-10)
            assert np.isclose(float(normalized_cosine.sum()), 1)
            assert bool((normalized_cosine.where(~available, 0) == 0).all())
            manual = (rain.fillna(0) * normalized_spherical).sum(("latitude", "longitude"))
            actual = rain.where(mask).weighted(np.cos(np.deg2rad(rain.latitude))).mean(("latitude", "longitude"))
            # The notebook reduces float32 data; allow 0.001 mm rounding error.
            np.testing.assert_allclose(actual, manual, atol=0.001, rtol=0)
            records.append({
                "region": name,
                "valid_cells": int(available.sum()),
                "available_area_fraction": float(spherical.where(available, 0).sum() / spherical.broadcast_like(valid).where(mask, 0).sum()),
                "maximum_monthly_mean_difference_mm": float(abs(actual - manual).max()),
            })
        report = {
            "total_cells": int(valid.size),
            "valid_cells": int(valid.sum()),
            "permanently_missing_cells": int((~present.any("time")).sum()),
            "intermittently_missing_cells": int(intermittent.sum()),
            "regions": records,
            "scope": "Rectangular study area; no Nigeria boundary mask. Missing coverage is not classified as land or ocean.",
        }
    destination = Path("results/spatial_weighting_checks.json")
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

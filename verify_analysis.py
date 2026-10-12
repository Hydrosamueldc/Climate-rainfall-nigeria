"""Audit historical predictions and compare OLS with HAC trend inference.

HAC uses Bartlett weights, n/(n-2) covariance correction and an approximate
t(33) reference. Lags 1, 3 and 5 are sensitivity choices, not tuned to results.
"""
from pathlib import Path
import json

import numpy as np
import pandas as pd
from scipy import stats
import statsmodels.api as sm
import xarray as xr


def trend_tests(values, years, lag=None):
    """Return slopes, standard errors and two-sided approximate p-values."""
    values = np.asarray(values, dtype=float)
    centered = years - years.mean()
    n = len(years)
    denominator = centered @ centered
    slopes = centered @ values / denominator
    residuals = values - values.mean(axis=0) - centered[:, None] * slopes
    if lag is None:
        variance = (residuals**2).sum(axis=0) / (n - 2) / denominator
    else:
        scores = centered[:, None] * residuals
        meat = (scores**2).sum(axis=0)
        for offset in range(1, lag + 1):
            weight = 1 - offset / (lag + 1)
            meat += 2 * weight * (scores[offset:] * scores[:-offset]).sum(axis=0)
        variance = meat / denominator**2 * n / (n - 2)
    standard_errors = np.sqrt(np.maximum(variance, 0))
    p_values = 2 * stats.t.sf(abs(slopes / standard_errors), n - 2)
    return slopes, standard_errors, p_values


def main():
    with xr.open_dataset('data/chirps_nigeria.nc') as dataset:
        rain = dataset.precip.where(dataset.precip >= 0)
        seasonal = rain.sel(time=rain.time.dt.month.isin([6, 7, 8, 9])).groupby('time.year').sum('time', min_count=4).load()
    years = seasonal.year.values.astype(float)
    np.testing.assert_array_equal(years, np.arange(1991, 2026))
    weights = np.cos(np.deg2rad(seasonal.latitude))
    yearly = seasonal.weighted(weights).mean(('latitude', 'longitude')).to_series()
    predictions = pd.read_csv('results/forecast_predictions.csv').set_index('year')
    np.testing.assert_array_equal(predictions.index, np.arange(2011, 2026))
    rows = []
    for year in predictions.index:
        history = yearly.loc[yearly.index < year]
        assert history.index.max() == year - 1
        lag_fit = np.polyfit(history.values[:-1], history.values[1:], 1)
        trend_fit = np.polyfit(history.index[1:], history.values[1:], 1)
        rows.append([yearly.loc[year], history.mean(), history.iloc[-1],
                     np.polyval(lag_fit, history.iloc[-1]), np.polyval(trend_fit, year)])
    reconstructed = pd.DataFrame(rows, index=predictions.index, columns=predictions.columns)
    # Published predictions are rounded to 0.1 mm; metrics use unrounded values.
    np.testing.assert_allclose(reconstructed, predictions, atol=0.051, rtol=0)
    metrics = pd.read_csv('results/metrics.csv').set_index('model')
    baseline_mse = np.mean((reconstructed.baseline - reconstructed.actual)**2)
    for model in metrics.index:
        errors = reconstructed[model] - reconstructed.actual
        calculated = [np.mean(abs(errors)), np.sqrt(np.mean(errors**2)), 1 - np.mean(errors**2) / baseline_mse]
        np.testing.assert_allclose(calculated, metrics.loc[model], atol=0.002, rtol=0)

    cells = seasonal.transpose('year', 'latitude', 'longitude').values.reshape(len(years), -1)
    cells = cells[:, np.isfinite(cells).all(axis=0)]
    comparisons = []
    regional = []
    ols_selected = None
    for lag in [None, 1, 3, 5]:
        slopes, errors, p_values = trend_tests(cells, years, lag)
        # Verify the vectorized estimator against independent library fits.
        for index in np.linspace(0, cells.shape[1] - 1, 12, dtype=int):
            fitted = sm.OLS(cells[:, index], sm.add_constant(years - years.mean())).fit()
            if lag is not None:
                fitted = fitted.get_robustcov_results(cov_type='HAC', maxlags=lag, use_correction=True, use_t=True)
            np.testing.assert_allclose([slopes[index], errors[index], p_values[index]],
                                       [fitted.params[1], fitted.bse[1], fitted.pvalues[1]], atol=1e-9, rtol=1e-7)
        selected = stats.false_discovery_control(p_values, method='bh') <= 0.05
        if lag is None:
            ols_selected = selected
        label = 'OLS' if lag is None else f'HAC_lag{lag}'
        comparisons.append({
            'method': label, 'valid_cells': cells.shape[1],
            'raw_p_below_005_fraction': float(np.mean(p_values < 0.05)),
            'bh_fdr_selected_fraction': float(selected.mean()),
            'selected_by_both_ols_and_method_fraction': float((selected & ols_selected).mean()),
            'selection_changed_from_ols_fraction': float((selected != ols_selected).mean()),
        })
        for name, data in [('study_box', seasonal), ('north', seasonal.where(seasonal.latitude >= 9)), ('south', seasonal.where(seasonal.latitude < 9))]:
            values = data.weighted(weights).mean(('latitude', 'longitude')).values[:, None]
            slope, error, p = trend_tests(values, years, lag)
            margin = stats.t.ppf(0.975, len(years) - 2) * error[0]
            regional.append({'region': name, 'method': label, 'slope_mm_per_decade': slope[0] * 10,
                             'approximate_p_value': p[0], 'ci95_low_mm_per_decade': (slope[0] - margin) * 10,
                             'ci95_high_mm_per_decade': (slope[0] + margin) * 10})
    destination = Path('results')
    pd.DataFrame(comparisons).to_csv(destination / 'trend_inference_sensitivity.csv', index=False)
    pd.DataFrame(regional).to_csv(destination / 'regional_trend_sensitivity.csv', index=False)
    report = {'forecast_years': len(predictions), 'forecast_predictions_verified': True,
              'forecast_metrics_verified': True, 'hac_library_cross_checks': 36,
              'method': 'Newey-West HAC, Bartlett kernel, lags 1/3/5, n/(n-2) correction, approximate t(n-2) inference; BH q=0.05 separately per method.',
              'limitations': 'Sensitivity analysis, not definitive inference. Small sample, bandwidth choice, temporal model and spatial dependence remain limitations. HAC may increase or decrease standard errors.',
              'trend_comparison': comparisons}
    (destination / 'analysis_verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()

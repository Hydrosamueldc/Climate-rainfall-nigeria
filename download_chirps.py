"""Download a CHIRPS v2 monthly subset from UCSB using HTTP range reads."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import itertools
import urllib.request
import zlib
import time

import fsspec
import h5py
import netCDF4
import numpy as np
import pandas as pd
import xarray as xr

SOURCE = ('https://data.chc.ucsb.edu/products/CHIRPS-2.0/'
          'global_monthly/netcdf/chirps-v2.0.monthly.nc')


def download_subset(destination, start, end, lat_min, lat_max, lon_min, lon_max):
    """Read compressed source chunks intersecting the requested rectangle."""
    destination = Path(destination)
    destination.parent.mkdir(exist_ok=True)
    temporary = destination.with_suffix('.download.nc')
    with fsspec.open(SOURCE, block_size=65536, cache_type='blockcache') as remote:
        with h5py.File(remote, 'r') as source:
            latitude = source['latitude'][:]
            longitude = source['longitude'][:]
            time_variable = source['time']
            dates = pd.DatetimeIndex(netCDF4.num2date(
                time_variable[:], time_variable.attrs['units'].decode(),
                only_use_cftime_datetimes=False, only_use_python_datetimes=True))
            requested_months = pd.period_range(start, end, freq='M')
            available_months = dates.to_period('M')
            missing = requested_months.difference(available_months)
            if len(missing):
                raise ValueError(f'Source does not yet contain requested months: {list(missing)}')
            def subset_indices(values, lower, upper):
                indices = np.flatnonzero((values >= lower) & (values <= upper))
                if not len(indices):
                    raise ValueError('Requested subset is empty.')
                return slice(int(indices[0]), int(indices[-1]) + 1)
            time_slice = subset_indices(dates, pd.Timestamp(start), pd.Timestamp(end))
            lat_slice = subset_indices(latitude, lat_min, lat_max)
            lon_slice = subset_indices(longitude, lon_min, lon_max)
            rainfall = source['precip']
            slices = [time_slice, lat_slice, lon_slice]
            chunk_shape = rainfall.chunks
            if rainfall.compression != 'gzip' or rainfall.shuffle or rainfall.fletcher32:
                raise ValueError('Source filters changed; inspect before decoding chunks.')
            tasks = []
            for origin in itertools.product(*(range(s.start // size * size, s.stop, size)
                                                for s, size in zip(slices, chunk_shape))):
                info = rainfall.id.get_chunk_info_by_coord(origin)
                if info.filter_mask != 0:
                    raise ValueError('Unexpected source filter mask.')
                tasks.append((origin, info.byte_offset, info.size))
            dtype = rainfall.dtype
            values = np.full(tuple(s.stop - s.start for s in slices), np.nan, dtype=dtype)
            print(f'Retrieving {len(tasks)} compressed blocks in parallel', flush=True)

            def fetch(task):
                origin, offset, size = task
                for attempt in range(3):
                    try:
                        request = urllib.request.Request(SOURCE, headers={
                            'Range': f'bytes={offset}-{offset + size - 1}'})
                        with urllib.request.urlopen(request, timeout=120) as response:
                            if response.status != 206:
                                raise ValueError('Server did not honor partial-file request.')
                            payload = response.read()
                        if len(payload) != size:
                            raise ValueError('Incomplete compressed block.')
                        block = np.frombuffer(zlib.decompress(payload), dtype=dtype).reshape(chunk_shape)
                        return origin, block
                    except Exception:
                        if attempt == 2:
                            raise
                        time.sleep(2)

            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(fetch, task) for task in tasks]
                for count, future in enumerate(as_completed(futures), 1):
                    origin, block = future.result()
                    lower = [max(s.start, o) for s, o in zip(slices, origin)]
                    upper = [min(s.stop, o + size) for s, o, size in zip(slices, origin, chunk_shape)]
                    target = tuple(slice(lo - s.start, hi - s.start) for lo, hi, s in zip(lower, upper, slices))
                    source_slice = tuple(slice(lo - o, hi - o) for lo, hi, o in zip(lower, upper, origin))
                    values[target] = block[source_slice]
                    print(f'Retrieved block {count}/{len(tasks)}', flush=True)
            values = np.where(values == rainfall.attrs['_FillValue'][0], np.nan, values)
            data = xr.DataArray(values, dims=('time', 'latitude', 'longitude'),
                coords={'time': dates[time_slice], 'latitude': latitude[lat_slice],
                        'longitude': longitude[lon_slice]}, name='precip',
                attrs={'units': 'mm/month', 'description': 'Monthly rainfall accumulation'})
            dataset = data.to_dataset()
            dataset.attrs.update(source=SOURCE, product='CHIRPS v2.0 monthly, 0.05 degree',
                                 download_method='HTTP range reads of UCSB source HDF5 chunks')
            dataset.to_netcdf(temporary, encoding={'precip': {'zlib': True, 'complevel': 4}})
    temporary.replace(destination)
    print('Saved', destination, flush=True)


if __name__ == '__main__':
    download_subset('data/chirps_nigeria.nc', '1991-01-01', '2025-12-01', 4, 14, 2.5, 15)

"""Execute the analysis with this Python environment and retain notebook outputs."""
import os
from pathlib import Path
import sys

import nbformat
from nbclient import NotebookClient

root = Path(__file__).resolve().parent
os.environ['PATH'] = str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH']
path = root / 'nigeria_rainfall_analysis.ipynb'
notebook = nbformat.read(path, as_version=4)


def report_cell(cell_index, **kwargs):
    print(f'Executing cell {cell_index + 1}/{len(notebook.cells)}', flush=True)


class EnvironmentNotebookClient(NotebookClient):
    """Run the kernel with the interpreter that launched this script."""

    def create_kernel_manager(self):
        manager = super().create_kernel_manager()
        manager.kernel_spec.argv = [sys.executable, '-m', 'ipykernel_launcher',
                                    '-f', '{connection_file}']
        return manager


print(f'Notebook kernel interpreter: {sys.executable}', flush=True)
client = EnvironmentNotebookClient(notebook, timeout=900, kernel_name='python3',
                        resources={'metadata': {'path': str(root)}},
                        on_cell_start=report_cell)
try:
    client.execute()
finally:
    nbformat.write(notebook, path)
print('Notebook execution completed and outputs saved.', flush=True)

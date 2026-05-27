"""
PyInstaller hook for ultralytics.
Collects all ultralytics data files (model configs, YAML files) and
hidden imports that are loaded dynamically.
"""
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files("ultralytics")
hiddenimports = collect_submodules("ultralytics")

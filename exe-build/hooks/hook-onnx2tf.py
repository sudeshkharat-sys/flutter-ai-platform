"""PyInstaller hook for onnx2tf."""
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files("onnx2tf")
hiddenimports = collect_submodules("onnx2tf")

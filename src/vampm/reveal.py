"""Ask Windows Explorer to open the parent and select the exact file."""
import ctypes
import os
from pathlib import Path


def reveal_file(path):
    path=Path(path).resolve()
    if not path.is_file():raise ValueError('文件已移动或不存在，请更新索引后重试。')
    if os.name!='nt':return {'opened':False,'selected':False}
    # Shell PIDLs avoid explorer.exe /select quoting and existing-window races.
    shell=ctypes.WinDLL('shell32',use_last_error=True)
    ole=ctypes.WinDLL('ole32',use_last_error=True)
    ole.CoInitializeEx.argtypes=[ctypes.c_void_p,ctypes.c_ulong];ole.CoInitializeEx.restype=ctypes.c_long
    ole.CoTaskMemFree.argtypes=[ctypes.c_void_p]
    shell.SHParseDisplayName.argtypes=[ctypes.c_wchar_p,ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p),ctypes.c_ulong,ctypes.c_void_p]
    shell.SHParseDisplayName.restype=ctypes.c_long
    shell.SHOpenFolderAndSelectItems.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_void_p,ctypes.c_ulong]
    shell.SHOpenFolderAndSelectItems.restype=ctypes.c_long
    initialized=ole.CoInitializeEx(None,2);pidl=ctypes.c_void_p()
    try:
        result=shell.SHParseDisplayName(str(path),None,ctypes.byref(pidl),0,None)
        if result<0:raise OSError('无法定位资源文件，Windows Shell 错误 '+hex(result & 0xffffffff))
        result=shell.SHOpenFolderAndSelectItems(pidl,0,None,0)
        if result<0:raise OSError('无法在资源管理器选中文件，Windows Shell 错误 '+hex(result & 0xffffffff))
        return {'opened':True,'selected':True}
    finally:
        if pidl:ole.CoTaskMemFree(pidl)
        if initialized>=0:ole.CoUninitialize()

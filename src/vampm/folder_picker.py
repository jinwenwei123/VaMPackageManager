"""Explorer-style Windows folder selection, without uploading or enumerating contents.

https://learn.microsoft.com/windows/win32/shell/common-file-dialog
"""
import ctypes
import os
import uuid
from pathlib import Path


class GUID(ctypes.Structure):
    _fields_ = [('data1', ctypes.c_uint32), ('data2', ctypes.c_uint16),
                ('data3', ctypes.c_uint16), ('data4', ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


def _call(pointer, index, types=(), args=()):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    method = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *types)(table[index])
    return method(pointer, *args)


def _check(result):
    if result < 0:
        raise OSError(f'无法打开 Windows 文件夹选择器（0x{result & 0xffffffff:08X}），可直接输入目录路径。')


def choose_folder(initial='', language='zh-CN'):
    if os.name != 'nt':
        raise ValueError('文件夹选择器仅在 Windows 中可用；当前环境请直接输入目录路径。')
    ole = ctypes.WinDLL('ole32')
    shell = ctypes.WinDLL('shell32')
    pointer = ctypes.c_void_p
    ole.CoInitializeEx.argtypes = [pointer, ctypes.c_uint32]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), pointer, ctypes.c_uint32, ctypes.POINTER(GUID), ctypes.POINTER(pointer)]
    ole.CoCreateInstance.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = [pointer]
    ole.CoTaskMemFree.restype = None
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    shell.SHCreateItemFromParsingName.argtypes = [ctypes.c_wchar_p, pointer, ctypes.POINTER(GUID), ctypes.POINTER(pointer)]
    shell.SHCreateItemFromParsingName.restype = ctypes.c_long
    _check(ole.CoInitializeEx(None, 2))  # COINIT_APARTMENTTHREADED, on this HTTP worker.
    dialog, folder, result, text = pointer(), pointer(), pointer(), pointer()
    try:
        clsid = GUID.parse('DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7')
        iid = GUID.parse('D57C7288-D4AD-4768-BE02-9D969532D960')  # IFileOpenDialog
        shell_iid = GUID.parse('43826D1E-E718-42EE-BC55-A1E261C37BFE')  # IShellItem
        _check(ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(dialog)))
        options = ctypes.c_uint32()
        _check(_call(dialog, 10, (ctypes.POINTER(ctypes.c_uint32),), (ctypes.byref(options),)))
        # PICKFOLDERS | FORCEFILESYSTEM | NOCHANGEDIR | PATHMUSTEXIST | DONTADDTORECENT
        _check(_call(dialog, 9, (ctypes.c_uint32,), (options.value | 0x2000868,)))
        _check(_call(dialog, 17, (ctypes.c_wchar_p,), (('Choose the VaM game root (containing AddonPackages)' if language=='en' else '选择 VaM 游戏根目录（包含 AddonPackages）'),)))
        _check(_call(dialog, 18, (ctypes.c_wchar_p,), (('Select this folder' if language=='en' else '选择此文件夹'),)))
        if initial and Path(initial).is_dir():
            _check(shell.SHCreateItemFromParsingName(str(Path(initial).resolve()), None, ctypes.byref(shell_iid), ctypes.byref(folder)))
            _check(_call(dialog, 12, (pointer,), (folder,)))  # SetFolder
        status = _call(dialog, 3, (pointer,), (None,))  # IModalWindow.Show
        if status & 0xffffffff == 0x800704C7: return None  # User cancelled.
        _check(status)
        _check(_call(dialog, 20, (ctypes.POINTER(pointer),), (ctypes.byref(result),)))  # GetResult
        _check(_call(result, 5, (ctypes.c_uint32, ctypes.POINTER(pointer)), (0x80058000, ctypes.byref(text))))
        return ctypes.wstring_at(text)
    finally:
        if text: ole.CoTaskMemFree(text)
        for obj in (result, folder, dialog):
            if obj: _call(obj, 2)  # IUnknown.Release
        ole.CoUninitialize()

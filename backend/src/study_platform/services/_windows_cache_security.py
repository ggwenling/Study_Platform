"""Windows ACLs for the private evaluation embedding cache."""

import msvcrt
import os
from contextlib import contextmanager

import pywintypes
import win32api
import win32con
import win32file
import win32security
import winerror


def _user_sid():
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        return win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()


def _private_attributes():
    sid = win32security.ConvertSidToStringSid(_user_sid())
    attributes = pywintypes.SECURITY_ATTRIBUTES()
    # Set the owner and use a protected DACL so the parent cannot grant access.
    attributes.SECURITY_DESCRIPTOR = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        f"O:{sid}D:P(A;OICI;FA;;;{sid})(A;OICI;FA;;;SY)", win32security.SDDL_REVISION_1
    )
    return attributes


def _is_private(handle):
    security = win32security.GetSecurityInfo(
        handle,
        win32security.SE_FILE_OBJECT,
        win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION,
    )
    user = _user_sid()
    dacl = security.GetSecurityDescriptorDacl()
    control, _ = security.GetSecurityDescriptorControl()
    if (
        security.GetSecurityDescriptorOwner() != user
        or not control & win32security.SE_DACL_PROTECTED
        or dacl is None
    ):
        return False
    system = win32security.CreateWellKnownSid(win32security.WinLocalSystemSid, None)
    for index in range(dacl.GetAceCount()):
        header, *_, sid = dacl.GetAce(index)
        # Reject unrecognized ACE types as well as grants to another principal.
        if header[0] != win32security.ACCESS_ALLOWED_ACE_TYPE or sid not in (user, system):
            return False
    return True


@contextmanager
def _open_existing(path, access):
    handle = win32file.CreateFile(
        str(path), access | win32con.READ_CONTROL, win32con.FILE_SHARE_READ,
        None, win32con.OPEN_EXISTING,
        win32con.FILE_FLAG_BACKUP_SEMANTICS | win32file.FILE_FLAG_OPEN_REPARSE_POINT, None,
    )
    try:
        yield handle
    finally:
        handle.Close()


def directory_error(path):
    try:
        win32file.CreateDirectory(str(path), _private_attributes())
    except pywintypes.error as exc:
        if exc.winerror not in (winerror.ERROR_ALREADY_EXISTS, winerror.ERROR_FILE_EXISTS):
            if exc.winerror == winerror.ERROR_ACCESS_DENIED:
                return "cache_permissions"
            raise
    try:
        with _open_existing(path, 0) as handle:
            attributes = win32file.GetFileInformationByHandle(handle)[0]
            if attributes & win32con.FILE_ATTRIBUTE_REPARSE_POINT or not (
                attributes & win32con.FILE_ATTRIBUTE_DIRECTORY
            ):
                return "cache_not_directory"
            return None if _is_private(handle) else "cache_permissions"
    except pywintypes.error as exc:
        if exc.winerror == winerror.ERROR_ACCESS_DENIED:
            return "cache_permissions"
        raise


def read_private(path):
    try:
        with _open_existing(path, win32con.GENERIC_READ) as handle:
            if win32file.GetFileType(handle) != win32con.FILE_TYPE_DISK:
                return None
            attributes = win32file.GetFileInformationByHandle(handle)[0]
            if attributes & (win32con.FILE_ATTRIBUTE_REPARSE_POINT | win32con.FILE_ATTRIBUTE_DIRECTORY):
                return None
            if not _is_private(handle):
                return None
            size = win32file.GetFileSize(handle)
            return win32file.ReadFile(handle, size)[1] if size else b""
    except pywintypes.error as exc:
        if exc.winerror in (
            winerror.ERROR_FILE_NOT_FOUND, winerror.ERROR_PATH_NOT_FOUND,
            winerror.ERROR_ACCESS_DENIED, winerror.ERROR_SHARING_VIOLATION,
        ):
            return None
        raise


def open_new(path):
    handle = win32file.CreateFile(
        str(path), win32con.GENERIC_WRITE, 0, _private_attributes(),
        win32con.CREATE_NEW, win32con.FILE_ATTRIBUTE_NORMAL, None,
    )
    try:
        descriptor = msvcrt.open_osfhandle(int(handle), os.O_WRONLY | os.O_BINARY)
        handle.Detach()  # The CRT descriptor now owns this handle.
        return descriptor
    finally:
        handle.Close()

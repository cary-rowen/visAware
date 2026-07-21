# Copyright (C) 2025-2026 Cary-rowen <manchen_0528@outlook.com>
# This file is covered by the GNU General Public License.
# See the file COPYING for more details.

"""Protect and unprotect secrets with the Windows Data Protection API."""

from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
from typing import Any

CRYPTPROTECT_UI_FORBIDDEN = 0x01
DEFAULT_DATA_DESCRIPTION = "Protected secret"

_MAX_DWORD = 0xFFFFFFFF
_LPBYTE = ctypes.POINTER(wintypes.BYTE)

__all__ = [
	"CRYPTPROTECT_UI_FORBIDDEN",
	"DEFAULT_DATA_DESCRIPTION",
	"SecureStorageError",
	"protectData",
	"protectString",
	"unprotectData",
	"unprotectString",
]


class SecureStorageError(Exception):
	"""Raised when protecting or unprotecting data fails."""


class _DATA_BLOB(ctypes.Structure):
	"""Windows DATA_BLOB structure used by CryptProtectData."""

	_fields_ = [
		("cbData", wintypes.DWORD),
		("pbData", _LPBYTE),
	]


class _DpapiBindings:
	"""Lazy ctypes bindings for the Windows DPAPI functions used here."""

	CryptProtectData: Any
	CryptUnprotectData: Any
	LocalFree: Any

	def __init__(self) -> None:
		super().__init__()
		try:
			crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
			kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
		except AttributeError as e:
			raise SecureStorageError("Windows DPAPI is unavailable on this platform.") from e

		self.CryptProtectData = crypt32.CryptProtectData
		self.CryptProtectData.argtypes = [
			ctypes.POINTER(_DATA_BLOB),
			wintypes.LPCWSTR,
			ctypes.POINTER(_DATA_BLOB),
			wintypes.LPVOID,
			wintypes.LPVOID,
			wintypes.DWORD,
			ctypes.POINTER(_DATA_BLOB),
		]
		self.CryptProtectData.restype = wintypes.BOOL

		self.CryptUnprotectData = crypt32.CryptUnprotectData
		self.CryptUnprotectData.argtypes = [
			ctypes.POINTER(_DATA_BLOB),
			ctypes.POINTER(wintypes.LPWSTR),
			ctypes.POINTER(_DATA_BLOB),
			wintypes.LPVOID,
			wintypes.LPVOID,
			wintypes.DWORD,
			ctypes.POINTER(_DATA_BLOB),
		]
		self.CryptUnprotectData.restype = wintypes.BOOL

		self.LocalFree = kernel32.LocalFree
		self.LocalFree.argtypes = [wintypes.HLOCAL]
		self.LocalFree.restype = wintypes.HLOCAL


_bindings: _DpapiBindings | None = None


def _getBindings() -> _DpapiBindings:
	global _bindings
	if _bindings is None:
		_bindings = _DpapiBindings()
	return _bindings


def _formatLastError() -> str:
	errorCode = ctypes.get_last_error()
	if not errorCode:
		return "unknown error"
	return f"{errorCode}: {ctypes.FormatError(errorCode).strip()}"


def _raiseLastError(functionName: str) -> None:
	raise SecureStorageError(f"{functionName} failed: {_formatLastError()}")


def _bytesToBlob(data: bytes) -> tuple[_DATA_BLOB, Any]:
	if len(data) > _MAX_DWORD:
		raise ValueError("DATA_BLOB input is too large.")
	buffer = ctypes.create_string_buffer(data, len(data))
	return _DATA_BLOB(len(data), ctypes.cast(buffer, _LPBYTE)), buffer


def _blobToBytes(blob: _DATA_BLOB) -> bytes:
	if not blob.pbData or blob.cbData == 0:
		return b""
	return ctypes.string_at(blob.pbData, blob.cbData)


def _freeBlob(blob: _DATA_BLOB) -> None:
	if not blob.pbData:
		return
	_getBindings().LocalFree(ctypes.cast(blob.pbData, wintypes.HLOCAL))
	blob.pbData = _LPBYTE()
	blob.cbData = 0


def protectData(
	data: bytes,
	description: str | None = DEFAULT_DATA_DESCRIPTION,
	optionalEntropy: bytes | None = None,
	flags: int = CRYPTPROTECT_UI_FORBIDDEN,
) -> bytes:
	"""
	Protects binary data with Windows DPAPI for the current user.

	:param data: Data to protect.
	:param description: Optional description stored with the protected data.
	:param optionalEntropy: Optional additional entropy required for decryption.
	:param flags: Flags passed to ``CryptProtectData``.
	:returns: Protected data bytes suitable for storage.
	:raises SecureStorageError: If Windows DPAPI fails.
	"""
	if not data:
		return b""

	bindings = _getBindings()
	blobIn, bufferIn = _bytesToBlob(data)
	entropyBlob = None
	entropyBuffer = None
	if optionalEntropy is not None:
		entropyBlob, entropyBuffer = _bytesToBlob(optionalEntropy)
	entropyPointer = ctypes.byref(entropyBlob) if entropyBlob is not None else None
	blobOut = _DATA_BLOB()

	try:
		if not bindings.CryptProtectData(
			ctypes.byref(blobIn),
			description,
			entropyPointer,
			None,
			None,
			flags,
			ctypes.byref(blobOut),
		):
			_raiseLastError("CryptProtectData")
		return _blobToBytes(blobOut)
	finally:
		_freeBlob(blobOut)
		del bufferIn
		del entropyBuffer


def unprotectData(
	protectedData: bytes,
	optionalEntropy: bytes | None = None,
	flags: int = CRYPTPROTECT_UI_FORBIDDEN,
) -> bytes:
	"""
	Unprotects binary data protected with Windows DPAPI.

	:param protectedData: Protected data returned by ``protectData``.
	:param optionalEntropy: Optional entropy that was used for protection.
	:param flags: Flags passed to ``CryptUnprotectData``.
	:returns: Unprotected data bytes.
	:raises SecureStorageError: If Windows DPAPI fails.
	"""
	if not protectedData:
		return b""

	bindings = _getBindings()
	blobIn, bufferIn = _bytesToBlob(protectedData)
	entropyBlob = None
	entropyBuffer = None
	if optionalEntropy is not None:
		entropyBlob, entropyBuffer = _bytesToBlob(optionalEntropy)
	entropyPointer = ctypes.byref(entropyBlob) if entropyBlob is not None else None
	blobOut = _DATA_BLOB()

	try:
		if not bindings.CryptUnprotectData(
			ctypes.byref(blobIn),
			None,
			entropyPointer,
			None,
			None,
			flags,
			ctypes.byref(blobOut),
		):
			_raiseLastError("CryptUnprotectData")
		return _blobToBytes(blobOut)
	finally:
		_freeBlob(blobOut)
		del bufferIn
		del entropyBuffer


def protectString(
	plainText: str,
	description: str | None = DEFAULT_DATA_DESCRIPTION,
	optionalEntropy: bytes | None = None,
	flags: int = CRYPTPROTECT_UI_FORBIDDEN,
) -> str:
	"""
	Protects a UTF-8 string and returns Base64 encoded DPAPI data.

	:param plainText: Text to protect.
	:param description: Optional description stored with the protected data.
	:param optionalEntropy: Optional additional entropy required for decryption.
	:param flags: Flags passed to ``CryptProtectData``.
	:returns: Base64 encoded protected data.
	:raises SecureStorageError: If Windows DPAPI fails.
	"""
	if not plainText:
		return ""
	protectedData = protectData(
		plainText.encode("utf-8"),
		description=description,
		optionalEntropy=optionalEntropy,
		flags=flags,
	)
	return base64.b64encode(protectedData).decode("ascii")


def unprotectString(
	protectedText: str,
	optionalEntropy: bytes | None = None,
	flags: int = CRYPTPROTECT_UI_FORBIDDEN,
) -> str:
	"""
	Unprotects a Base64 encoded DPAPI string.

	:param protectedText: Base64 encoded protected data.
	:param optionalEntropy: Optional entropy that was used for protection.
	:param flags: Flags passed to ``CryptUnprotectData``.
	:returns: Unprotected UTF-8 text.
	:raises SecureStorageError: If the protected text cannot be unprotected as UTF-8 text.
	"""
	if not protectedText:
		return ""
	try:
		protectedData = base64.b64decode(protectedText.encode("ascii"), validate=True)
	except (UnicodeError, ValueError) as e:
		raise SecureStorageError("Protected text is not valid Base64.") from e
	try:
		return unprotectData(protectedData, optionalEntropy=optionalEntropy, flags=flags).decode("utf-8")
	except UnicodeError as e:
		raise SecureStorageError("Unprotected data is not valid UTF-8.") from e

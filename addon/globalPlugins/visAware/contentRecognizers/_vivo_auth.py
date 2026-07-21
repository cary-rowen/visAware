# Copyright (C) 2025, Cary-rowen from NVDACN
#
# This module handles the generation of authentication headers required
# for the VIVO API. It interfaces with the NVDACN API to securely
# obtain a signature.

"""Handles authentication header generation for the VIVO API via NVDACN."""

import json
import random
import string
import time
import urllib.parse

import config
from logHandler import log
from .. import network
from ..exceptions import AuthenticationError, NetworkError, ApiError
from ..secure_storage import SecureStorageError, unprotectString

__all__ = ["genSignHeaders", "getNvdacnCredentials"]

NVDACN_API_URL = "https://nvdacn.com/api/"
VIVO_APP_ID = "3046775094"
AUTH_REQUEST_TIMEOUT = 6  # Seconds for a single authentication request attempt


def getNvdacnCredentials() -> tuple[str, str]:
	"""
	Returns configured NVDACN credentials.

	:raises AuthenticationError: If NVDACN credentials are not configured.
	"""
	generalConf = config.conf["visAwareGeneral"]
	user = generalConf["nvdacnUser"]
	try:
		password = unprotectString(generalConf["nvdacnPass"])
	except SecureStorageError:
		password = ""
	if not user or not password:
		# Translators: An error message if NVDACN credentials are not set.
		raise AuthenticationError(_("Please configure your NVDACN account in the General OCR settings."))
	return user, password


def _genNonce(length: int = 8) -> str:
	"""
	Generates a random alphanumeric string of a given length.

	:param length: The desired length of the nonce.
	:returns: A random alphanumeric string.
	"""
	chars = string.ascii_lowercase + string.digits
	return "".join(random.choice(chars) for _ in range(length))


def _genCanonicalQueryString(params: dict) -> str:
	"""
	Creates a sorted, URL-encoded query string for signature consistency.

	:param params: A dictionary of query parameters.
	:returns: The canonical query string.
	"""
	if not params:
		return ""
	sortedParams = sorted(params.items())
	return "&".join(f"{urllib.parse.quote(k)}={urllib.parse.quote(str(v))}" for k, v in sortedParams)


def _fetchSignatureFromService(nvdacnUser: str, nvdacnPass: str, signingStringBytes: bytes) -> str:
	"""
	Fetches the signature from the NVDACN API.

	:param nvdacnUser: The user's NVDACN username.
	:param nvdacnPass: The user's NVDACN password.
	:param signingStringBytes: The byte string to be signed.
	:returns: The fetched signature string.
	:raises AuthenticationError: If the NVDACN API returns an authentication error.
	:raises ApiError: If the response from the server is invalid.
	"""
	apiParams = {"user": nvdacnUser, "pass": nvdacnPass, "name": "vivo", "action": "signature"}
	url = f"{NVDACN_API_URL}?{urllib.parse.urlencode(apiParams)}"

	log.debug("Requesting Vivo signature from NVDACN API for user: %s", nvdacnUser)

	try:
		response = network.sendRequest(
			method="POST",
			url=url,
			data=signingStringBytes,
			timeout=AUTH_REQUEST_TIMEOUT,
		)
		result = response.json()

		if result.get("code") == 200 and "data" in result:
			log.info("Successfully fetched Vivo signature for user %s.", nvdacnUser)
			return result["data"]
		else:
			errorMessage = result.get("data", "Unknown API error")
			raise AuthenticationError(f"NVDACN API Error: {errorMessage} (Code: {result.get('code')})")

	except AuthenticationError:
		raise
	except (NetworkError, ApiError) as e:
		log.error(
			"A network error or API error occurred while fetching Vivo signature for user: %s.",
			nvdacnUser,
			exc_info=True,
		)
		# Translators: An error message indicating failure to connect to the authentication server.
		raise AuthenticationError(_("Could not connect to the authentication server.")) from e
	except (json.JSONDecodeError, KeyError, TypeError) as e:
		log.error("Invalid response from NVDACN API: %s", response.text, exc_info=True)
		# Translators: An error message for an invalid response from the authentication server.
		raise ApiError(_("Invalid response from the authentication server.")) from e


def genSignHeaders(nvdacnUser: str, nvdacnPass: str, method: str, uri: str, query: dict) -> dict:
	"""
	Generates the complete set of authentication headers for the VIVO API.

	:param nvdacnUser: The NVDACN username.
	:param nvdacnPass: The NVDACN password.
	:param method: The HTTP method (e.g., "POST").
	:param uri: The request URI path (e.g., "/ocr/general_recognition").
	:param query: A dictionary of query parameters.
	:returns: A dictionary containing the required authentication headers.
	"""
	method = str(method).upper()
	timestamp = str(int(time.time()))
	nonce = _genNonce()

	canonicalQueryString = _genCanonicalQueryString(query)
	signedHeadersString = (
		f"x-ai-gateway-app-id:{VIVO_APP_ID}\nx-ai-gateway-timestamp:{timestamp}\nx-ai-gateway-nonce:{nonce}"
	)
	signingString = (
		f"{method}\n{uri}\n{canonicalQueryString}\n{VIVO_APP_ID}\n{timestamp}\n{signedHeadersString}"
	)
	signingStringBytes = signingString.encode("utf-8")

	signature = _fetchSignatureFromService(nvdacnUser, nvdacnPass, signingStringBytes)

	return {
		"X-AI-GATEWAY-APP-ID": VIVO_APP_ID,
		"X-AI-GATEWAY-TIMESTAMP": timestamp,
		"X-AI-GATEWAY-NONCE": nonce,
		"X-AI-GATEWAY-SIGNED-HEADERS": "x-ai-gateway-app-id;x-ai-gateway-timestamp;x-ai-gateway-nonce",
		"X-AI-GATEWAY-SIGNATURE": signature,
	}

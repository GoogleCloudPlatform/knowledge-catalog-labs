import contextvars
import logging
import threading

import google.auth
import google.oauth2.credentials

logger = logging.getLogger(__name__)

_oauth_token = contextvars.ContextVar("oauth_token", default=None)
_cached_adc_creds = None
_adc_lock = threading.Lock()


def set_oauth_token(token: str | dict | None):
    """Sets the OAuth token for the current context."""
    _oauth_token.set(token)


def get_oauth_token() -> str | None:
    """Gets the OAuth access token string from the current context."""
    val = _oauth_token.get()
    if isinstance(val, dict):
        return val.get("access_token")
    return val


def get_credentials(quota_project_id: str):
    """
    Returns Google Credentials object.
    Only uses the stored OAuth token if it contains a refresh_token along with
    GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET (required by Google Cloud clients for refresh).
    Otherwise, seamlessly uses Application Default Credentials (ADC).
    """
    import os

    token_data = _oauth_token.get()
    if (
        token_data
        and isinstance(token_data, dict)
        and os.environ.get("BYPASS_OAUTH") != "true"
    ):
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        client_id = os.environ.get("GOOGLE_CLIENT_ID")
        client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")

        if access_token and refresh_token and client_id and client_secret:
            return google.oauth2.credentials.Credentials(
                token=access_token,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                quota_project_id=quota_project_id,
            )

    global _cached_adc_creds
    if not _cached_adc_creds:
        with _adc_lock:
            if not _cached_adc_creds:
                logger.info(
                    "Loading Application Default Credentials (ADC)..."
                )
                try:
                    _cached_adc_creds, _ = google.auth.default(
                        quota_project_id=quota_project_id
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to load Application Default Credentials: {e}"
                    )
                    _cached_adc_creds = None

    return _cached_adc_creds

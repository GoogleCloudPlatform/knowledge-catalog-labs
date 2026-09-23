import os
import time
import unittest
from unittest.mock import MagicMock, patch

import gradio as gr
import pandas as pd

from metadata_propagation.agent.plugins.context import (
    get_credentials,
    reset_oauth_context,
    set_oauth_token,
)
from metadata_propagation.agent.plugins.lineage_plugin import LineagePlugin
from metadata_propagation.ui.gradio_app import (
    apply_policy_tag_recommendations,
    get_token_from_session,
    scan_dataset,
)


class TestAuthBypassADCFallback(unittest.TestCase):
    def setUp(self):
        reset_oauth_context()
        self._orig_env = os.environ.copy()
        os.environ.pop("BYPASS_OAUTH", None)
        os.environ["GOOGLE_CLIENT_ID"] = "real-google-client-id.apps.googleusercontent.com"
        os.environ["GOOGLE_CLIENT_SECRET"] = "real-google-client-secret"

    def tearDown(self):
        reset_oauth_context()
        os.environ.clear()
        os.environ.update(self._orig_env)

    @patch("google.auth.default")
    def test_get_credentials_blocks_adc_fallback_when_token_is_none(self, mock_adc):
        """Verify get_credentials raises PermissionError and never calls google.auth.default when token is None."""
        set_oauth_token(None)
        with self.assertRaises(PermissionError):
            get_credentials("test-project")
        mock_adc.assert_not_called()

    @patch("google.auth.default")
    def test_get_credentials_blocks_adc_when_token_is_empty_or_invalid_dict(self, mock_adc):
        """Verify get_credentials blocks ADC when set_oauth_token is called with an empty dict or empty string."""
        set_oauth_token({})
        with self.assertRaises(PermissionError):
            get_credentials("test-project")
        set_oauth_token("")
        with self.assertRaises(PermissionError):
            get_credentials("test-project")
        mock_adc.assert_not_called()

    @patch("google.auth.default")
    def test_get_token_from_session_rejects_empty_session(self, mock_adc):
        """Verify get_token_from_session raises gr.Error when request.session lacks google_token."""
        mock_request = MagicMock(spec=gr.Request)
        mock_request.session = {}

        with self.assertRaises(gr.Error) as ctx:
            get_token_from_session(mock_request)
        self.assertIn("Authentication required", str(ctx.exception))
        mock_adc.assert_not_called()

    @patch("google.auth.default")
    def test_get_token_from_session_rejects_expired_token_without_refresh(self, mock_adc):
        """Verify expired session tokens without refresh_token raise gr.Error instead of falling back to ADC."""
        mock_request = MagicMock(spec=gr.Request)
        mock_request.session = {
            "google_token": {
                "access_token": "expired-token",
                "expires_at": time.time() - 120,
            }
        }

        with self.assertRaises(gr.Error) as ctx:
            get_token_from_session(mock_request)
        self.assertIn("Session expired", str(ctx.exception))
        self.assertNotIn("google_token", mock_request.session)
        mock_adc.assert_not_called()

    @patch("google.auth.default")
    @patch("google.cloud.bigquery.Client")
    def test_full_taint_chain_scan_dataset_blocks_unauthenticated_caller(
        self, mock_bq_client, mock_adc
    ):
        """Reproduce the reported taint chain on scan_dataset and verify BigQuery client is never initialized with ADC."""
        unauthenticated_request = MagicMock(spec=gr.Request)
        unauthenticated_request.session = {}

        with self.assertRaises(gr.Error):
            scan_dataset(
                "test-project",
                "europe-west1",
                "test_dataset",
                request=unauthenticated_request,
            )

        mock_adc.assert_not_called()
        mock_bq_client.assert_not_called()

    @patch("google.auth.default")
    @patch("google.cloud.bigquery.Client")
    def test_lineage_plugin_blocks_adc_when_oauth_token_none(
        self, mock_bq_client, mock_adc
    ):
        """Verify LineagePlugin.scan_for_missing_descriptions raises PermissionError if set_oauth_token(None) was called."""
        set_oauth_token(None)
        plugin = LineagePlugin("test-project", "europe-west1")

        with self.assertRaises(PermissionError):
            plugin.scan_for_missing_descriptions("test_dataset")

        mock_adc.assert_not_called()
        mock_bq_client.assert_not_called()

    @patch("google.auth.default")
    def test_apply_policy_tag_recommendations_blocks_unauthenticated_caller(
        self, mock_adc
    ):
        """Verify apply_policy_tag_recommendations cannot be used by an unauthenticated attacker to escalate privileges."""
        unauthenticated_request = MagicMock(spec=gr.Request)
        unauthenticated_request.session = {}
        df = pd.DataFrame(
            {
                "Select": [True],
                "Target Column": ["ssn"],
                "Policy Tags": ["projects/p/locations/l/taxonomies/1/policyTags/2"],
            }
        )

        with self.assertRaises(gr.Error):
            apply_policy_tag_recommendations(
                "test-project",
                "europe-west1",
                "test_dataset",
                "test_table",
                df,
                "user:attacker@example.com",
                request=unauthenticated_request,
            )

        mock_adc.assert_not_called()

    @patch("google.auth.default")
    def test_get_credentials_accepts_valid_dict_and_str_tokens(self, mock_adc):
        """Verify valid user OAuth tokens (both dict and worker-thread string tokens) return user Credentials without ADC."""
        set_oauth_token({"access_token": "user-access-token-123"})
        creds = get_credentials("test-project")
        self.assertEqual(creds.token, "user-access-token-123")
        mock_adc.assert_not_called()

        # Worker thread string token propagation
        set_oauth_token("worker-thread-token-456")
        worker_creds = get_credentials("test-project")
        self.assertEqual(worker_creds.token, "worker-thread-token-456")
        mock_adc.assert_not_called()


if __name__ == "__main__":
    unittest.main()

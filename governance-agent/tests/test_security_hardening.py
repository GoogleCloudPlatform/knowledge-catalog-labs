import os
import pathlib
import stat
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import metadata_propagation.ui.gradio_app as ga
from metadata_propagation.agent.plugins.context import (
    reset_oauth_context,
    set_oauth_token,
)
from metadata_propagation.agent.plugins.doc_description_plugin import (
    DocDescriptionPlugin,
)
from metadata_propagation.agent.plugins.document_rag_engine import (
    DocumentRAGEngine,
)
from metadata_propagation.agent.plugins.lineage_plugin import LineagePlugin
from metadata_propagation.dataplex_integration.lineage_propagation import (
    LineageGraphTraverser,
    TransformationEnricher,
)


class TestSSRFAndURLInjectionDefense(unittest.TestCase):
    @patch(
        "metadata_propagation.dataplex_integration.lineage_propagation.requests.post"
    )
    def test_search_links_blocks_ssrf_and_token_exfiltration(self, mock_post):
        malicious_cases = [
            ("test-project", "attacker.example.com#"),
            ("test-project", "attacker.example.com/steal?"),
            ("test-project", "169.254.169.254#"),
            ("test-project", "europe-west1@evil.com"),
            ("test-project", "EUROPE-WEST1"),
            ("proj/../../evil", "europe-west1"),
            ("proj?foo=bar", "europe-west1"),
            ("proj#frag", "europe-west1"),
            ("proj..evil", "europe-west1"),
        ]
        for proj, loc in malicious_cases:
            with self.subTest(project_id=proj, location=loc):
                traverser = LineageGraphTraverser(
                    proj, loc, token="secret-oauth-bearer-token"
                )
                with self.assertRaises(ValueError):
                    traverser._search_links("bigquery:p.d.t")
        mock_post.assert_not_called()

    @patch(
        "metadata_propagation.dataplex_integration.lineage_propagation.requests.post"
    )
    def test_search_links_allows_valid_location_and_sets_timeout(
        self, mock_post
    ):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"links": []}
        mock_post.return_value = mock_resp

        traverser = LineageGraphTraverser(
            "test-project", "europe-west1", token="tok"
        )
        links = traverser._search_links("bigquery:test-project.ds.tbl")

        self.assertEqual(links, [])
        mock_post.assert_called_once()
        url = mock_post.call_args[0][0]
        self.assertEqual(
            url,
            "https://europe-west1-datalineage.googleapis.com/v1/projects/test-project/locations/europe-west1:searchLinks",
        )
        self.assertEqual(mock_post.call_args[1]["timeout"], 30)

    def test_datastore_search_url_validation(self):
        plugin = DocDescriptionPlugin("test-project", "europe-west1")
        plugin.datastore_id = "my-datastore_1"
        self.assertIn(
            "/projects/test-project/locations/global/collections/default_collection/dataStores/my-datastore_1/",
            plugin._build_datastore_search_url(),
        )

        plugin.datastore_id = (
            "projects/test-project/locations/global/collections/"
            "default_collection/dataStores/my-ds"
        )
        self.assertTrue(
            plugin._build_datastore_search_url().startswith(
                "https://discoveryengine.googleapis.com/v1/projects/test-project/"
            )
        )

        for bad_id in (
            "../evil",
            "ds?key=val",
            "ds#frag",
            "projects/p/locations/global/dataStores/../../secret",
            "projects/p/evil/path",
        ):
            with self.subTest(datastore_id=bad_id):
                plugin.datastore_id = bad_id
                with self.assertRaises(ValueError):
                    plugin._build_datastore_search_url()


class TestUploadedDocPathContainment(unittest.TestCase):
    def test_get_active_doc_paths_blocks_outside_and_disallowed_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            upload_dir = root / "uploads"
            upload_dir.mkdir()

            valid_doc = upload_dir / "abc12345_spec.md"
            valid_doc.write_text("# Valid Spec")

            bad_ext_doc = upload_dir / "abc12345_secret.env"
            bad_ext_doc.write_text("SECRET=1")

            outside_doc = root / "outside.md"
            outside_doc.write_text("TOP SECRET")

            symlink_escape = upload_dir / "abc12345_link.md"
            symlink_escape.symlink_to(outside_doc)

            docs_state = [
                {"label": "valid", "path": str(valid_doc)},
                {"label": "bad_ext", "path": str(bad_ext_doc)},
                {"label": "outside", "path": str(outside_doc)},
                {"label": "symlink", "path": str(symlink_escape)},
                {"label": "etc", "path": "/etc/passwd"},
                {"label": "nul", "path": f"{valid_doc}\x00.md"},
            ]
            selected = [d["label"] for d in docs_state]

            with patch.object(ga, "UPLOAD_DIR", str(upload_dir)):
                active = ga.get_active_doc_paths(docs_state, selected)

            self.assertEqual(active, [str(valid_doc.resolve())])


class TestWebSecurityHeadersAndCookies(unittest.TestCase):
    def setUp(self):
        reset_oauth_context()
        self._orig_env = os.environ.copy()
        os.environ.pop("BYPASS_OAUTH", None)
        os.environ.pop("K_SERVICE", None)
        os.environ.pop("SESSION_COOKIE_SECURE", None)
        os.environ["GOOGLE_CLIENT_ID"] = "cid.apps.googleusercontent.com"
        os.environ["GOOGLE_CLIENT_SECRET"] = "csecret"
        os.environ["SESSION_SECRET_KEY"] = "test-secret"

    def tearDown(self):
        ga.require_oauth_globally(False)
        reset_oauth_context()
        os.environ.clear()
        os.environ.update(self._orig_env)

    def test_security_headers_present_on_responses(self):
        from fastapi.testclient import TestClient

        client = TestClient(ga.create_app())
        for resp in (
            client.get("/"),
            client.post(
                "/gradio_api/call/scan_dataset",
                json={"data": ["p", "l", "d", None, None]},
            ),
        ):
            self.assertEqual(
                resp.headers.get("X-Content-Type-Options"), "nosniff"
            )
            self.assertEqual(resp.headers.get("X-Frame-Options"), "SAMEORIGIN")
            self.assertEqual(
                resp.headers.get("Referrer-Policy"),
                "strict-origin-when-cross-origin",
            )

    def test_session_cookie_https_only_follows_deployment_context(self):
        from starlette.middleware.sessions import SessionMiddleware

        def _get_session_middleware_kwargs(app):
            for mw in app.user_middleware:
                if mw.cls is SessionMiddleware:
                    return mw.kwargs
            self.fail("SessionMiddleware not found")

        os.environ["GOOGLE_REDIRECT_URI"] = (
            "http://localhost:7860/google_callback"
        )
        local_kw = _get_session_middleware_kwargs(ga.create_app())
        self.assertFalse(local_kw["https_only"])
        self.assertEqual(local_kw["same_site"], "lax")

        os.environ["GOOGLE_REDIRECT_URI"] = (
            "https://governance.example.com/google_callback"
        )
        prod_kw = _get_session_middleware_kwargs(ga.create_app())
        self.assertTrue(prod_kw["https_only"])

        os.environ["GOOGLE_REDIRECT_URI"] = (
            "http://localhost:7860/google_callback"
        )
        os.environ["K_SERVICE"] = "governance-agent"
        cloud_run_kw = _get_session_middleware_kwargs(ga.create_app())
        self.assertTrue(cloud_run_kw["https_only"])


class TestOAuthCredentialsAndRegexSafety(unittest.TestCase):
    def setUp(self):
        reset_oauth_context()
        self._orig_env = os.environ.copy()
        os.environ.pop("BYPASS_OAUTH", None)

    def tearDown(self):
        reset_oauth_context()
        os.environ.clear()
        os.environ.update(self._orig_env)

    @patch("google.auth.default")
    @patch(
        "metadata_propagation.dataplex_integration.insights_connector.bigquery.Client"
    )
    @patch(
        "metadata_propagation.dataplex_integration.insights_connector.dataplex_v1.DataScanServiceClient"
    )
    @patch(
        "metadata_propagation.dataplex_integration.lineage_propagation.bigquery.Client"
    )
    @patch("metadata_propagation.agent.plugins.lineage_plugin.bigquery.Client")
    def test_lineage_plugin_passes_oauth_creds_to_description_propagator(
        self,
        mock_lp_bq,
        mock_sf_bq,
        mock_ds_client,
        mock_ic_bq,
        mock_adc,
    ):
        set_oauth_token({"access_token": "user-oauth-tok"})
        plugin = LineagePlugin("test-project", "europe-west1")
        plugin._ensure_initialized()

        mock_adc.assert_not_called()
        ds_creds = mock_ds_client.call_args[1]["credentials"]
        ic_bq_creds = mock_ic_bq.call_args[1]["credentials"]
        self.assertEqual(ds_creds.token, "user-oauth-tok")
        self.assertEqual(ic_bq_creds.token, "user-oauth-tok")

    def test_extract_column_logic_handles_regex_metacharacters_safely(self):
        sql = "SELECT a * 2 AS amount FROM t"
        for malicious_col in ("[unclosed", "(a+)+$", "amount.*"):
            with self.subTest(col=malicious_col):
                self.assertIsNone(
                    TransformationEnricher.extract_column_logic(
                        sql, malicious_col
                    )
                )

    def test_rag_cache_uses_sha256_and_restrictive_permissions(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            src_doc = root / "spec.md"
            src_doc.write_text("# Table: orders\nOrder details")

            engine = DocumentRAGEngine.__new__(DocumentRAGEngine)
            mock_client = MagicMock()
            mock_client.models.generate_content.return_value = MagicMock(
                text="# Extracted", usage_metadata=None
            )
            engine._get_client = lambda: mock_client

            with patch(
                "metadata_propagation.agent.plugins.document_rag_engine.tempfile.gettempdir",
                return_value=str(root),
            ):
                extracted = engine._extract_text_via_gemini(str(src_doc))

            self.assertEqual(extracted, "# Extracted")
            cache_dir = root / "governance_agent_rag_cache"
            self.assertEqual(stat.S_IMODE(cache_dir.stat().st_mode), 0o700)
            cached_files = list(cache_dir.glob("*.md"))
            self.assertEqual(len(cached_files), 1)
            # SHA-256 hex digest is 64 chars (vs 32 chars for MD5)
            stem_parts = cached_files[0].name.split(".")
            self.assertEqual(len(stem_parts[-2]), 64)
            self.assertEqual(
                stat.S_IMODE(cached_files[0].stat().st_mode), 0o600
            )


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import MagicMock, patch

from metadata_propagation.agent.plugins.dq_plugin import DQPlugin
from metadata_propagation.dataplex_integration.dq_propagation import (
    DQPropagationEngine,
)


class TestDQPropagation(unittest.TestCase):
    def setUp(self):
        self.project_id = "test-project"
        self.location = "europe-west1"
        self.dq_plugin = DQPlugin(self.project_id, self.location)
        self.engine = DQPropagationEngine(self.project_id, self.location)

    @patch("metadata_propagation.agent.plugins.dq_plugin.get_credentials")
    @patch("google.cloud.dataplex_v1.DataScanServiceClient")
    def test_fetch_dq_summary_auto_dq(self, mock_client_class, mock_creds):
        mock_client = mock_client_class.return_value

        # Mock List Job
        mock_job = MagicMock()
        mock_job.name = "job-1"
        mock_job.state = MagicMock(name="SUCCEEDED")
        # Ensure name comparison works
        mock_job.state.name = "SUCCEEDED"

        mock_client.list_data_scan_jobs.return_value = [mock_job]

        # Mock Get Job (with results)
        mock_job_full = MagicMock()
        mock_job_full.state = MagicMock(name="SUCCEEDED")
        mock_job_full.state.name = "SUCCEEDED"

        # Configure the pb response for MessageToDict
        # We'll just mock the return value of get_latest_dq_job instead of deep protobuf mocking
        with patch.object(DQPlugin, "get_latest_dq_job") as mock_get_job:
            mock_get_job.return_value = {
                "dataQualityResult": {
                    "passed": True,
                    "dimensions": [
                        {"dimension": "COMPLETENESS", "passed": True},
                        {"dimension": "UNIQUENESS", "passed": False},
                    ],
                },
                "endTime": "2024-03-20T10:00:00Z",
            }

            summary = self.dq_plugin.fetch_dq_summary("ds", "tab")
            self.assertEqual(summary["source"], "AUTO_DQ")
            self.assertEqual(summary["score"], 1.0)
            self.assertEqual(summary["dimensions"]["COMPLETENESS"], 1.0)

    def test_aggregation_conservative_min(self):
        scores = [0.9, 0.7, 1.0]
        result = self.engine.aggregate_scores(scores, method="conservative_min")
        self.assertEqual(result, 0.7)

    @patch(
        "metadata_propagation.dataplex_integration.dq_propagation.SQLFetcher.get_transformation_sql"
    )
    def test_detect_remediation_distinct(self, mock_sql):
        # Mock SQL with DISTINCT
        mock_sql.return_value = (
            "CREATE TABLE t AS SELECT DISTINCT id, name FROM src"
        )

        bonus = self.engine.detect_remediation("ds", "table", "id")
        self.assertEqual(bonus, 0.1)

    @patch("google.cloud.bigquery.Client")
    @patch("metadata_propagation.agent.plugins.context.get_credentials")
    def test_history_bq_insert(self, mock_creds, mock_bq_client):
        mock_client_instance = mock_bq_client.return_value
        # Mock insert_rows_json to return no errors
        mock_client_instance.insert_rows_json.return_value = []

        fqn = "bigquery:p.d.t"
        col = "c1"
        self.engine.update_history(
            fqn, col, 0.75, "DERIVED", {"COMPLETENESS": 1.0}
        )

        # Verify BQ insert was called
        mock_client_instance.insert_rows_json.assert_called_once()
        args, _kwargs = mock_client_instance.insert_rows_json.call_args
        rows = args[1]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["table_fqn"], fqn)
        self.assertEqual(rows[0]["column_name"], col)
        self.assertEqual(rows[0]["dq_score"], 0.75)

    @patch("google.cloud.bigquery.Client")
    @patch("metadata_propagation.agent.plugins.context.get_credentials")
    def test_trend_calculation(self, mock_creds, mock_bq_client):
        mock_client_instance = mock_bq_client.return_value

        fqn = "bigquery:p.d.t"
        col = "c1"

        # Mock the BQ query job to return improving trend data
        mock_query_job = MagicMock()
        # Newer first, older second: 0.9 > 0.7 + 0.05
        mock_row_1 = MagicMock()
        mock_row_1.dq_score = 0.9
        mock_row_2 = MagicMock()
        mock_row_2.dq_score = 0.7
        mock_query_job.__iter__.return_value = [mock_row_1, mock_row_2]

        mock_client_instance.query.return_value = mock_query_job

        trend = self.engine.get_trend(fqn, col)
        self.assertEqual(trend, "improving")

        # Also test degrading
        mock_row_1.dq_score = 0.5
        mock_row_2.dq_score = 0.8
        trend = self.engine.get_trend(fqn, col)
        self.assertEqual(trend, "degrading")

        # And stable
        mock_row_1.dq_score = 0.81
        mock_row_2.dq_score = 0.80
        trend = self.engine.get_trend(fqn, col)
        self.assertEqual(trend, "stable")

    @patch.object(DQPlugin, "_get_bq_client")
    def test_check_completeness_bq_valid_identifiers(self, mock_get_bq_client):
        mock_client = mock_get_bq_client.return_value
        mock_row = MagicMock(total=10, non_null=8)
        mock_client.query.return_value.result.return_value = [mock_row]

        score = self.dq_plugin._check_completeness_bq(
            "retail_ds", "orders_2026", "order_amount"
        )

        self.assertEqual(score, 0.8)
        mock_client.query.assert_called_once()
        sql = mock_client.query.call_args[0][0]
        self.assertIn("COUNT(`order_amount`) as non_null", sql)
        self.assertIn("FROM `test-project.retail_ds.orders_2026`", sql)

    @patch.object(DQPlugin, "_get_bq_client")
    def test_check_completeness_bq_rejects_sqli_and_invalid_identifiers(
        self, mock_get_bq_client
    ):
        invalid_cases = [
            # SQLi payloads in column_name (lineage source.field[] vector)
            ("ds", "tab", "1) FROM `p.d.secret`; DROP TABLE x; --"),
            ("ds", "tab", "col`) UNION SELECT 1, 1 --"),
            ("ds", "tab", "col; SELECT 1"),
            ("ds", "tab", "col\n--"),
            # SQLi payloads in table_name and dataset_id
            ("ds", "tab` UNION ALL SELECT 1, 1 --", "col"),
            ("ds`; DROP TABLE x; --", "tab", "col"),
            # Boundary and type checks
            ("ds", "tab", ""),
            ("ds", "tab", "1invalid_leading_digit"),
            ("ds", "tab", "a" * 301),
            ("ds", "tab", "has-hyphen"),
            ("ds", "tab", None),
        ]

        for ds, tab, col in invalid_cases:
            with self.subTest(dataset_id=ds, table_name=tab, column_name=col):
                with self.assertRaises(ValueError):
                    self.dq_plugin._check_completeness_bq(ds, tab, col)

        bad_project_plugin = DQPlugin("proj`; DROP TABLE x; --", self.location)
        with self.assertRaises(ValueError):
            bad_project_plugin._check_completeness_bq("ds", "tab", "col")

        mock_get_bq_client.assert_not_called()

    @patch.object(DQPlugin, "_get_bq_client")
    @patch.object(DQPlugin, "get_latest_profile_job", return_value=None)
    @patch.object(DQPlugin, "get_latest_dq_job", return_value=None)
    def test_fetch_dq_summary_bq_fallback_blocks_injection(
        self, mock_dq, mock_profile, mock_get_bq_client
    ):
        with self.assertRaises(ValueError):
            self.dq_plugin.fetch_dq_summary(
                "retail_ds",
                "orders",
                "id) FROM `p.d.t` WHERE 1=1 --",
            )
        mock_get_bq_client.assert_not_called()


if __name__ == "__main__":
    unittest.main()

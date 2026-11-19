from metadata_propagation.agent.plugins.similarity_engine import (
    SimilarityEngine,
)


def test_similarity_logic():
    engine = SimilarityEngine()

    # Test Lexical Match
    score_lex = engine.calculate_lexical_similarity(
        "customer_id", "Customer Identifier"
    )
    print(f"Lexical Match (customer_id, Customer Identifier): {score_lex}")
    assert score_lex > 0

    # Test Semantic Match (Placeholder)
    col_meta = {
        "name": "order_amount",
        "description": "Total price of the order",
    }
    term = {
        "name": "t-order-total",
        "display_name": "Order Total",
        "description": "The final amount charged for the order",
    }
    score_sem = engine.calculate_semantic_similarity(col_meta, term)
    print(f"Semantic Match (order_amount, Order Total): {score_sem}")
    assert score_sem > 0


def test_concept_mismatch():
    engine = SimilarityEngine()

    # transaction_id should NOT match Transaction Timestamp due to concept mismatch
    col = {"name": "transaction_id", "description": "ID of the transaction"}
    term = {
        "name": "t-ts",
        "display_name": "Transaction Timestamp",
        "description": "When the transaction happened",
    }

    # We need to simulate semantic score if embeddings aren't active in tests
    # In get_ranked_suggestions, lexical + semantic + boost - penalty
    suggestions = engine.get_ranked_suggestions(col, [term])
    print("\nSuggestions for 'transaction_id' with 'Transaction Timestamp':")
    for s in suggestions:
        print(f"- {s['display_name']} (Confidence: {s['confidence']})")

    # Should be filtered out or have very low confidence
    assert len(suggestions) == 0 or suggestions[0]["confidence"] < 0.3


def test_recommendation_ranking():
    engine = SimilarityEngine()

    col = {"name": "membership_level", "description": "Customer loyalty status"}
    terms = [
        {
            "name": "t1",
            "display_name": "Loyalty Tier",
            "description": "Rank of customer membership",
        },
        {
            "name": "t2",
            "display_name": "Product SKU",
            "description": "Stock keeping unit",
        },
        {
            "name": "t3",
            "display_name": "Customer Identifier",
            "description": "Internal ID for customer",
        },
    ]

    suggestions = engine.get_ranked_suggestions(col, terms)
    print("\nRanked Suggestions for 'membership_level':")
    for s in suggestions:
        print(f"- {s['display_name']} (Confidence: {s['confidence']})")

    assert suggestions[0]["display_name"] == "Loyalty Tier"


def test_glossary_cache_delete_uses_array_query_parameter(tmp_path):
    from unittest.mock import MagicMock

    from metadata_propagation.agent.plugins.glossary_plugin import (
        GlossaryPlugin,
    )

    plugin = GlossaryPlugin(
        "test-project",
        "europe-west1",
        cache_dataset_id="metadata_ds",
        cache_table_id="glossary_cache",
    )
    plugin._bq_client = MagicMock()
    plugin._get_local_cache_path = lambda: str(tmp_path / "cache.json")

    malicious_term_id = "term' OR 1=1); DROP TABLE x; --"
    entries = {
        malicious_term_id: {"hash_val": "abc", "embedding": [0.1, 0.2]},
    }
    plugin._save_hybrid_cache(entries, entries)

    plugin._bq_client.query.assert_called_once()
    sql = plugin._bq_client.query.call_args[0][0]
    job_config = plugin._bq_client.query.call_args[1]["job_config"]
    assert "WHERE term_id IN UNNEST(@term_ids)" in sql
    assert malicious_term_id not in sql
    assert job_config.query_parameters[0].name == "term_ids"
    assert job_config.query_parameters[0].values == [malicious_term_id]


def test_glossary_cache_rejects_invalid_bq_identifiers(tmp_path):
    from unittest.mock import MagicMock

    import pytest

    from metadata_propagation.agent.plugins.glossary_plugin import (
        GlossaryPlugin,
    )

    bad_cases = [
        ("proj`; DROP TABLE x; --", "ds", "tbl"),
        ("test-project", "ds`; DROP TABLE x; --", "tbl"),
        ("test-project", "ds", "tbl`; DROP TABLE x; --"),
    ]
    for idx, (proj, ds, tbl) in enumerate(bad_cases):
        plugin = GlossaryPlugin(
            proj,
            "europe-west1",
            cache_dataset_id=ds,
            cache_table_id=tbl,
        )
        plugin._bq_client = MagicMock()
        cache_file = tmp_path / f"missing_{idx}.json"
        plugin._get_local_cache_path = lambda p=cache_file: str(p)
        with pytest.raises(ValueError):
            plugin._load_hybrid_cache()
        with pytest.raises(ValueError):
            plugin._save_hybrid_cache(
                {}, {"t1": {"hash_val": "h", "embedding": []}}
            )
        plugin._bq_client.query.assert_not_called()


if __name__ == "__main__":
    print("Running Similarity Engine Tests...")
    test_similarity_logic()
    test_recommendation_ranking()
    test_concept_mismatch()
    print("\nAll logical tests passed!")

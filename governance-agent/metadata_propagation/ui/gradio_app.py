import os

# Force gRPC to use native IPv4 resolver to bypass macOS IPv6 lookup hangs/timeouts
os.environ["GRPC_DNS_RESOLVER"] = "native"
os.environ["GRPC_IPv6"] = "off"

import logging

import fastapi
import gradio as gr
import pandas as pd
from authlib.integrations.starlette_client import OAuth
from dotenv import load_dotenv
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

# Load environment variables
load_dotenv(override=True)

# --- Custom OAuth Setup ---
oauth_config = OAuth()
oauth_config.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={
        "scope": "openid email profile https://www.googleapis.com/auth/bigquery https://www.googleapis.com/auth/cloud-platform"
    },
)
# ---------------------------

# Import Agent Components
from metadata_propagation.agent.plugins.context import (
    set_oauth_token,
)
from metadata_propagation.agent.plugins.dq_plugin import DQPlugin
from metadata_propagation.agent.plugins.glossary_plugin import (
    GlossaryPlugin,
)
from metadata_propagation.agent.plugins.lineage_plugin import (
    LineagePlugin,
)
from metadata_propagation.agent.plugins.policy_tag_plugin import (
    PolicyTagPlugin,
)
from metadata_propagation.dataplex_integration.dq_propagation import (
    DQPropagationEngine,
)

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Fetch Project ID from environment or default
DEFAULT_PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "governance-agent")
DEFAULT_LOCATION = "europe-west1"
DEFAULT_DATASET_ID = os.environ.get(
    "BIGQUERY_DATASET_ID", "retail_synthetic_data"
)

_candidate_paths = [
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../../knowledge_insights.json")),
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../knowledge_insights.json")),
    os.path.abspath("knowledge_insights.json"),
]
KNOWLEDGE_JSON_PATH = next((p for p in _candidate_paths if os.path.exists(p)), _candidate_paths[0])


def handle_refresh_lineage_cache():
    from lineage_propagation import LineageGraphTraverser

    LineageGraphTraverser.clear_global_cache()
    gr.Info("Unified Lineage Cache cleared successfully!")
    return "Unified Lineage Cache cleared."


def get_plugin(project_id, location):
    return LineagePlugin(
        project_id, location, knowledge_json_path=KNOWLEDGE_JSON_PATH
    )


def get_token_from_session(request: gr.Request):
    if request:
        return request.session.get("google_token", {}).get("access_token")
    return None


def scan_dataset(
    project_id,
    location,
    dataset_id,
    cache_dataset_id=None,
    cache_table_id=None,
    request: gr.Request = None,
):
    token = get_token_from_session(request)
    set_oauth_token(token)
    try:
        lineage_plugin = get_plugin(project_id, location)
        glossary_plugin = GlossaryPlugin(
            project_id,
            location,
            cache_dataset_id=cache_dataset_id,
            cache_table_id=cache_table_id,
        )

        # 1. Scan for missing technical descriptions
        desc_df = lineage_plugin.scan_for_missing_descriptions(dataset_id)

        # 2. Scan for missing glossary terms
        glossary_df = glossary_plugin.scan_for_missing_glossary_terms(
            dataset_id
        )

        # 3. Calculate "Orphaned" Columns (No description AND no glossary term)
        if not desc_df.empty and not glossary_df.empty:
            orphans_df = pd.merge(
                desc_df, glossary_df, on=["Table", "Column"], how="inner"
            )
        else:
            orphans_df = pd.DataFrame(columns=["Table", "Column"])

        # 4. Aggregate by Table for metrics
        desc_agg = (
            desc_df.groupby("Table")
            .size()
            .reset_index(name="Missing Descriptions")
            if not desc_df.empty
            else pd.DataFrame(columns=["Table", "Missing Descriptions"])
        )
        gloss_agg = (
            glossary_df.groupby("Table")
            .size()
            .reset_index(name="Missing Glossary Mappings")
            if not glossary_df.empty
            else pd.DataFrame(columns=["Table", "Missing Glossary Mappings"])
        )
        orphan_agg = (
            orphans_df.groupby("Table")
            .size()
            .reset_index(name="Orphaned Columns")
            if not orphans_df.empty
            else pd.DataFrame(columns=["Table", "Orphaned Columns"])
        )

        # 5. Summary and Metrics
        desc_count = len(desc_df)
        gloss_count = len(glossary_df)
        orphan_count = len(orphans_df)

        if desc_count == 0 and gloss_count == 0:
            summary = "✅ **Metadata Estate is Complete!** All objects have both technical descriptions and business glossary mappings."
        else:
            summary = "### 📊 Governance Gap Analysis\n"
            summary += f"We found **{desc_count}** column gaps in technical descriptions and **{gloss_count}** column gaps in business glossary mappings.\n\n"

            if not desc_agg.empty:
                summary += f"🔍 **Technical Gaps**: {len(desc_agg)} objects affected.\n"
            if not gloss_agg.empty:
                summary += f"📖 **Business Gaps**: {len(gloss_agg)} objects affected.\n"

            summary += "\n*Detailed column recommendations are available in the 'Description Propagation' and 'Glossary Recommendations' tabs.*"

        return (
            summary,
            desc_agg,
            gloss_agg,
            orphan_agg,
            str(desc_count),
            str(gloss_count),
            str(orphan_count),
        )
    except Exception as e:
        logger.error(f"Scan failed: {e}")
        raise gr.Error(f"Scan failed: {e!s}")


GCP_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&display=swap');

/* --- Theme CSS Variables --- */
:root {
    --gcp-bg: #f8f9fa;
    --gcp-card-bg: #ffffff;
    --gcp-border: #dadce0;
    --gcp-text: #202124;
    --gcp-header-bg: #f1f3f4;
    --gcp-primary: #1a73e8;
    --gcp-primary-hover: #1765cc;
    --gcp-secondary-bg: #f1f3f4;
    --gcp-secondary-hover: #e8eaed;
    --gcp-code-bg: rgba(0,0,0,0.05);
    --gcp-metric-value-color: #1a73e8;
    --gcp-metric-label-color: #5f6368;
}

.dark {
    --gcp-bg: #202124;
    --gcp-card-bg: #2d2e30;
    --gcp-border: #3c4043;
    --gcp-text: #e8eaed;
    --gcp-header-bg: #303134;
    --gcp-primary: #8ab4f8;
    --gcp-primary-hover: #aecbfa;
    --gcp-secondary-bg: #3c4043;
    --gcp-secondary-hover: #4e5256;
    --gcp-code-bg: rgba(255,255,255,0.1);
    --gcp-metric-value-color: #8ab4f8;
    --gcp-metric-label-color: #9aa0a6;
}

/* --- Global Overrides --- */
* {
    font-family: 'Roboto', sans-serif !important;
}

body, .gradio-container {
    background-color: var(--gcp-bg) !important;
    background: var(--gcp-bg) !important;
    color: var(--gcp-text) !important;
}

/* --- Remove ALL Gradio Orange/Black/Low-Contrast --- */
:root, .gradio-container, body, .dark, .dark :root {
    --primary-50: var(--gcp-bg) !important;
    --primary-500: var(--gcp-primary) !important;
    --secondary-500: var(--gcp-primary) !important;
    --accent-500: var(--gcp-primary) !important;
    --body-background-fill: var(--gcp-bg) !important;
    --block-background-fill: var(--gcp-card-bg) !important;
    --block-border-color: var(--gcp-border) !important;
    --body-text-color: var(--gcp-text) !important;
    --block-label-text-color: var(--gcp-text) !important;
    --input-text-color: var(--gcp-text) !important;
    --button-primary-text-color: #ffffff !important;
    --button-secondary-text-color: var(--gcp-text) !important;
    --background-fill-primary: var(--gcp-card-bg) !important;
    --background-fill-secondary: var(--gcp-bg) !important;
}

/* Ensure text readability on main containers */
body, .gradio-container, p, span, div, h1, h2, h3, h4, h5, h6 {
    color: var(--gcp-text) !important;
}

/* Specific enforcement for Primary Buttons - White Text on Blue */
.primary, .gr-button-primary, button.primary, .lg.primary, .sm.primary,
button[variant="primary"], .gr-button-primary *, button.primary *,
.gradio-container button.primary, .gradio-container .primary {
    color: #ffffff !important;
    fill: #ffffff !important;
    background-color: var(--gcp-primary) !important;
}

.primary span, .gr-button-primary span, button.primary span,
.primary div, .gr-button-primary div, button.primary div {
    color: #ffffff !important;
}

.primary:hover, .gr-button-primary:hover, button.primary:hover {
    background-color: var(--gcp-primary-hover) !important;
    color: #ffffff !important;
}

.gr-button-secondary, .gr-button-secondary *, button.secondary, button.secondary * {
    color: var(--gcp-text) !important;
    background-color: var(--gcp-secondary-bg) !important;
    border: 1px solid var(--gcp-border) !important;
}

.gr-button-secondary:hover, button.secondary:hover {
    background-color: var(--gcp-secondary-hover) !important;
}

input, textarea, select, .gr-input, .gr-box, .gr-textbox input, .gr-textbox textarea {
    background-color: var(--gcp-card-bg) !important;
    color: var(--gcp-text) !important;
    border: 1px solid var(--gcp-border) !important;
}

/* Clean Gradio Dropdown & Select styling - single border, no nested padding/border */
.gradio-container .dropdown-container,
.gradio-container .dropdown-container .wrap,
.gradio-container .dropdown-container select,
.gradio-container .dropdown-container .wrap-inner,
.gradio-container .dropdown-container input {
    border: none !important;
    box-shadow: none !important;
    background-color: transparent !important;
}

.gradio-container .dropdown-container {
    border: 1px solid var(--gcp-border) !important;
    border-radius: 4px !important;
    background-color: var(--gcp-card-bg) !important;
}

.gradio-container .dropdown-container .options {
    background-color: var(--gcp-card-bg) !important;
    border: 1px solid var(--gcp-border) !important;
    color: var(--gcp-text) !important;
}

.gradio-container .dropdown-container .item {
    color: var(--gcp-text) !important;
}

.gradio-container .dropdown-container .item:hover {
    background-color: var(--gcp-secondary-hover) !important;
}

.gr-label, .block label, span[data-testid="block-info"], .gr-form label, .desc-markdown p {
    color: var(--gcp-text) !important;
    font-weight: 500 !important;
    font-size: 13px !important;
}

.gr-table, .gr-table-container, table, .dataframe, thead, tbody, tr, th, td {
    background-color: var(--gcp-card-bg) !important;
    background: var(--gcp-card-bg) !important;
    color: var(--gcp-text) !important;
    border-color: var(--gcp-border) !important;
}

th, thead th, .gr-table thead th, .dataframe thead th, 
.gr-table th, .dataframe th, [class*="thead"] th {
    background-color: var(--gcp-header-bg) !important;
    background: var(--gcp-header-bg) !important;
    color: var(--gcp-text) !important;
    font-weight: 500 !important;
    text-transform: uppercase !important;
    font-size: 11px !important;
    border-bottom: 2px solid var(--gcp-border) !important;
    padding: 12px 8px !important;
}

/* Force dark text in all header children specifically */
th span, th div, .gr-table th span, .gr-table th div,
.dataframe th span, .dataframe th div {
    color: var(--gcp-text) !important;
}

tbody td, .dark tbody td, tbody td span, tbody td div {
    color: var(--gcp-text) !important;
}

input[type="checkbox"] {
    cursor: pointer !important;
    appearance: checkbox !important;
    accent-color: var(--gcp-primary) !important;
    opacity: 1 !important;
    visibility: visible !important;
}

tr, .gr-table tr, .dataframe tr {
    background-color: var(--gcp-card-bg) !important;
}

.markdown code, .prose code, .markdown span, .prose span {
    background-color: var(--gcp-code-bg) !important;
    color: var(--gcp-text) !important;
    padding: 2px 4px !important;
    border-radius: 4px !important;
}

[style*="background-color: black"], [style*="background: black"], .bg-black {
    background-color: var(--gcp-bg) !important;
    color: var(--gcp-text) !important;
}

.tabs .tabitem.selected, .tabs button.selected {
    border-bottom: 3px solid var(--gcp-primary) !important;
    color: var(--gcp-primary) !important;
    background: transparent !important;
}

.tabs button {
    color: var(--gcp-metric-label-color) !important;
    border-bottom: 1px solid transparent !important;
}

.gcp-card {
    background: var(--gcp-card-bg) !important;
    border: 1px solid var(--gcp-border) !important;
    box-shadow: none !important;
    padding: 12px 16px !important;
}

.gcp-card .prose, .gcp-card .markdown {
    margin: 0 !important;
    padding: 0 !important;
}

.gcp-card h3 {
    margin: 0 0 8px 0 !important;
}

.gcp-card .block, .gcp-card .form, .gcp-card .dataframe {
    margin: 0 !important;
    padding: 0 !important;
}

.gcp-metric-card {
    background: var(--gcp-card-bg) !important;
    border: 1px solid var(--gcp-border) !important;
    border-radius: 8px !important;
    padding: 24px 16px !important;
    text-align: center !important;
}

.gcp-metric-value {
    color: var(--gcp-metric-value-color) !important;
    font-size: 36px !important;
    font-weight: 500 !important;
}

.gcp-metric-label {
    color: var(--gcp-metric-label-color) !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    text-transform: uppercase !important;
}
</style>
"""


def analyze_and_preview(
    project_id, location, dataset_id, target_table, request: gr.Request = None
):
    token = get_token_from_session(request)
    set_oauth_token(token)
    try:
        plugin = get_plugin(project_id, location)
        summary = plugin.get_lineage_summary(dataset_id, target_table)
        df = plugin.preview_propagation(dataset_id, target_table)
        if df.empty:
            gr.Warning(f"No upstream candidates found for {target_table}.")
            return summary, pd.DataFrame(
                columns=[
                    "Select",
                    "Target Column",
                    "Source",
                    "Source Column",
                    "Confidence",
                    "Proposed Description",
                    "Type",
                ]
            )
        df.insert(0, "Select", [True] * len(df))
        return summary, df
    except Exception as e:
        logger.error(f"Analyze & Preview failed: {e}")
        if "Not found" in str(e) or "404" in str(e):
            raise gr.Error(
                f"Table '{target_table}' does not exist in dataset '{dataset_id}'."
            )
        raise gr.Error(f"Operation failed: {e!s}")


def apply_propagation_improved(
    project_id,
    location,
    dataset_id,
    target_table,
    candidates_df,
    request: gr.Request = None,
):
    token = get_token_from_session(request)
    set_oauth_token(token)
    try:
        if candidates_df is None or candidates_df.empty:
            raise gr.Error("No candidates to apply.")
        candidates_df["Select"] = candidates_df["Select"].astype(bool)
        selected = candidates_df[candidates_df["Select"]]
        logger.info(
            f"Applying propagation: {len(selected)} selected rows out of {len(candidates_df)}"
        )
        if selected.empty:
            gr.Warning("No columns selected for application.")
            return "No columns selected."
        plugin = get_plugin(project_id, location)
        updates = []
        for _, row in selected.iterrows():
            if "Target Column" in row and "Proposed Description" in row:
                updates.append(
                    {
                        "table": target_table,
                        "column": row["Target Column"],
                        "description": row["Proposed Description"],
                    }
                )
        if not updates:
            return "No valid updates found in selection."
        plugin.apply_propagation(dataset_id, updates)
        return f"Successfully applied {len(updates)} updates to {target_table}!"
    except Exception as e:
        logger.error(f"Apply failed: {e}")
        raise gr.Error(f"Apply failed: {e!s}")


def select_all_lineage(df):
    if df is not None and not df.empty:
        df = df.copy()
        df["Select"] = [True] * len(df)
    return df


def deselect_all_lineage(df):
    if df is not None and not df.empty:
        df = df.copy()
        df["Select"] = [False] * len(df)
    return df


def get_glossary_recommendations(
    project_id,
    location,
    dataset_id,
    table_id,
    min_confidence=0.5,
    cache_dataset_id=None,
    cache_table_id=None,
    request: gr.Request = None,
):
    logger.info(
        f"get_glossary_recommendations called with project_id={project_id}, location={location}, dataset_id={dataset_id}, table_id={table_id}, min_confidence={min_confidence} (type: {type(min_confidence)})"
    )
    token = get_token_from_session(request)
    set_oauth_token(token)
    try:
        plugin = GlossaryPlugin(
            project_id,
            location,
            cache_dataset_id=cache_dataset_id,
            cache_table_id=cache_table_id,
        )
        df = plugin.recommend_terms_for_table(
            dataset_id, table_id, min_confidence=float(min_confidence)
        )
        if df.empty:
            gr.Info(
                f"No glossary recommendations found for {table_id} with confidence >= {min_confidence}."
            )
            return pd.DataFrame(
                columns=[
                    "Select",
                    "Column",
                    "Suggested Term",
                    "Confidence",
                    "Rationale",
                    "Term ID",
                ]
            )
        df.insert(0, "Select", [True] * len(df))
        return df
    except Exception as e:
        logger.error(f"Glossary recommendations failed: {e}")
        raise gr.Error(f"Operation failed: {e!s}")


def apply_glossary_selections(
    project_id,
    location,
    dataset_id,
    table_id,
    reco_df,
    cache_dataset_id=None,
    cache_table_id=None,
    request: gr.Request = None,
):
    token = get_token_from_session(request)
    set_oauth_token(token)
    try:
        if reco_df is None or reco_df.empty:
            raise gr.Error("No recommendations to apply.")
        reco_df["Select"] = reco_df["Select"].astype(bool)
        selected = reco_df[reco_df["Select"]]
        if selected.empty:
            gr.Warning("No terms selected for application.")
            return "No terms selected."
        plugin = GlossaryPlugin(
            project_id,
            location,
            cache_dataset_id=cache_dataset_id,
            cache_table_id=cache_table_id,
        )
        updates = []
        for _, row in selected.iterrows():
            updates.append(
                {
                    "column": row["Column"],
                    "term_id": row["Term ID"],
                    "term_display": row["Suggested Term"],
                }
            )
        plugin.apply_terms(dataset_id, table_id, updates)
        return f"Successfully applied {len(updates)} glossary terms to {table_id} in Knowledge Catalog!"
    except Exception as e:
        logger.error(f"Glossary apply failed: {e}")
        raise gr.Error(f"Apply failed: {e!s}")


def select_all_glossary(df):
    if df is not None and not df.empty:
        df = df.copy()
        df["Select"] = [True] * len(df)
    return df


def deselect_all_glossary(df):
    if df is not None and not df.empty:
        df = df.copy()
        df["Select"] = [False] * len(df)
    return df


def get_policy_tag_recommendations(
    project_id, location, dataset_id, table_id, request: gr.Request = None
):
    token = get_token_from_session(request)
    set_oauth_token(token)
    try:
        plugin = PolicyTagPlugin(project_id, location)
        df = plugin.preview_policy_tag_propagation(dataset_id, table_id)
        if df.empty:
            gr.Info(f"No policy tag recommendations found for {table_id}.")
            return pd.DataFrame(
                columns=[
                    "Select",
                    "Target Column",
                    "Source Table",
                    "Policy Tags",
                    "Recommendation",
                    "Logic",
                    "Access Summary",
                ]
            )
        df.insert(0, "Select", [True] * len(df))
        return df
    except Exception as e:
        logger.error(f"Policy tag recommendations failed: {e}")
        raise gr.Error(f"Operation failed: {e!s}")


def apply_policy_tag_recommendations(
    project_id,
    location,
    dataset_id,
    target_table,
    recommendations_df,
    additional_readers,
    request: gr.Request = None,
):
    token = get_token_from_session(request)
    set_oauth_token(token)
    try:
        if recommendations_df is None or recommendations_df.empty:
            raise gr.Error("No recommendations to apply.")
        recommendations_df["Select"] = recommendations_df["Select"].astype(bool)
        selected = recommendations_df[recommendations_df["Select"]]
        if selected.empty:
            gr.Warning("No columns selected for application.")
            return "No columns selected."
        plugin = PolicyTagPlugin(project_id, location)
        updates = []
        for _, row in selected.iterrows():
            update = {
                "table": target_table,
                "column": row["Target Column"],
                "policy_tag": row["Policy Tags"].split(", ")[0],
            }
            all_readers = []
            if additional_readers:
                all_readers.extend(
                    [
                        r.strip()
                        for r in additional_readers.split(",")
                        if r.strip()
                    ]
                )
            if all_readers:
                update["readers"] = list(set(all_readers))
            updates.append(update)
        plugin.apply_policy_tags(dataset_id, updates)
        return f"Successfully applied {len(updates)} policy tags to {target_table}!"
    except Exception as e:
        logger.error(f"Policy tag apply failed: {e}")
        raise gr.Error(f"Apply failed: {e!s}")


def select_all_policy(df):
    if df is not None and not df.empty:
        df = df.copy()
        df["Select"] = [True] * len(df)
    return df


def deselect_all_policy(df):
    if df is not None and not df.empty:
        df = df.copy()
        df["Select"] = [False] * len(df)
    return df


def get_dq_propagation(
    project_id, location, dataset_id, table_id, request: gr.Request = None
):
    token = get_token_from_session(request)
    set_oauth_token(token)
    try:
        dq_plugin = DQPlugin(project_id, location)
        engine = DQPropagationEngine(project_id, location, token=token)
        target_fqn = f"bigquery:{project_id}.{dataset_id}.{table_id}"
        from google.cloud import bigquery

        from metadata_propagation.agent.plugins.context import (
            get_credentials,
        )

        client = bigquery.Client(
            project=project_id, credentials=get_credentials(project_id)
        )
        table = client.get_table(f"{project_id}.{dataset_id}.{table_id}")
        columns = [f.name for f in table.schema]
        propagation_data = engine.propagate_dq_scores(
            target_fqn, dataset_id, table_id, columns
        )
        results = []
        for col in columns:
            data = propagation_data.get(col, {})
            leaves = data.get("leaves", [])
            bonus = data.get("bonus", 0.0)
            if leaves:
                best_conf = max(leaf.get("confidence", 0) for leaf in leaves)
                leaves = [
                    leaf
                    for leaf in leaves
                    if leaf.get("confidence", 0) >= best_conf
                ]
            upstream_scores = []
            source_names = []
            for leaf in leaves:
                src_parts = leaf["source_entity"].split(".")
                if len(src_parts) == 3:
                    s_ds, s_tab = src_parts[1], src_parts[2]
                    s_summary = dq_plugin.fetch_dq_summary(
                        s_ds, s_tab, leaf.get("source_column")
                    )
                    upstream_scores.append(s_summary["score"])
                    source_names.append(f"{s_tab}.{leaf.get('source_column')}")
            if not upstream_scores:
                summary = dq_plugin.fetch_dq_summary(dataset_id, table_id, col)
                base_score = summary["score"]
                source_type = summary["source"]
            else:
                base_score = engine.aggregate_scores(upstream_scores)
                source_type = "DERIVED"
            final_score = min(base_score + bonus, 1.0)
            engine.update_history(
                target_fqn, col, final_score, source_type=source_type
            )
            trend = engine.get_trend(target_fqn, col)
            badge = (
                "🟢 High"
                if final_score > 0.9
                else ("🟡 Medium" if final_score > 0.7 else "🔴 Low")
            )
            if bonus > 0:
                if base_score >= 1.0:
                    bonus_str = f"+{int(bonus * 100)}% (Capped)"
                else:
                    bonus_str = f"+{int(bonus * 100)}%"
            else:
                bonus_str = "None"
            results.append(
                {
                    "Column": col,
                    "Trust Score": round(final_score, 2),
                    "Badge": badge,
                    "Trend": trend.capitalize(),
                    "Bonus (Remediation)": bonus_str,
                    "Upstream Sources": ", ".join(source_names[:2])
                    + ("..." if len(source_names) > 2 else "")
                    or "None (Source)",
                }
            )
        return pd.DataFrame(results)
    except Exception as e:
        logger.error(f"DQ propagation failed: {e}")
        raise gr.Error(f"Operation failed: {e!s}")


with gr.Blocks(title="Governance on Auto-pilot") as demo:
    gr.HTML(GCP_CSS)

    with gr.Column(visible=True) as login_view:
        with gr.Column(elem_classes=["gcp-card"]):
            gr.Markdown("# Welcome to Governance on Auto-pilot")
            gr.Markdown(
                "Proactively manage your metadata and governance at scale."
            )
            login_btn = gr.Button(
                "Login with Google",
                variant="primary",
                elem_classes=["gr-button-primary"],
            )
            login_btn.click(
                lambda: gr.Info("Redirecting to Google Login..."), None, None
            ).then(fn=None, js="() => window.location.href='/google_login'")

    with gr.Column(visible=False) as app_view:
        with gr.Row():
            with gr.Column(scale=8):
                gr.Markdown("# 🛡️ Governance on Auto-pilot")
            with gr.Column(scale=2):
                logout_html = '<a href="/logout" style="color: #666; text-decoration: underline;">Logout</a>'
                gr.HTML(logout_html)

        with gr.Accordion("Global Environment Settings", open=True):
            with gr.Row():
                config_project = gr.Dropdown(
                    label="Project ID",
                    choices=[DEFAULT_PROJECT_ID],
                    value=DEFAULT_PROJECT_ID,
                    allow_custom_value=True,
                )
                config_location = gr.Dropdown(
                    label="Location",
                    choices=[
                        "us-central1",
                        "europe-west1",
                        "us-east1",
                        "us-west1",
                        "europe-west2",
                        "asia-east1",
                    ],
                    value=DEFAULT_LOCATION,
                    allow_custom_value=True,
                )
            with gr.Row():
                config_cache_dataset = gr.Textbox(
                    label="Glossary Cache Dataset ID",
                    value="",
                    placeholder="Default: Active Scanned Dataset ID",
                    info="Optional: Redirect embeddings cache storage to a separate writable dataset",
                )
                config_cache_table = gr.Textbox(
                    label="Glossary Cache Table ID",
                    value="glossary_embeddings_cache",
                    info="Configure custom BigQuery table name for glossary embeddings cache",
                )
                refresh_lineage_btn = gr.Button(
                    "🔄 Refresh Lineage Cache",
                    variant="secondary",
                    size="sm",
                    elem_classes=["gr-button-secondary"],
                )

            refresh_lineage_btn.click(
                handle_refresh_lineage_cache, inputs=None, outputs=None
            )

        with gr.Tabs():
            with gr.TabItem("Dashboard"):
                with gr.Column(elem_classes=["gcp-card"]):
                    gr.Markdown("## 📋 Data Estate Governance")
                    with gr.Group():
                        with gr.Row():
                            global_dataset = gr.Textbox(
                                label="Active Dataset ID",
                                value=DEFAULT_DATASET_ID,
                                placeholder="e.g. retail_synthetic_data",
                                info="Select a dataset to scan for gaps in descriptions and glossary mappings.",
                            )

                    with gr.Row():
                        scan_btn = gr.Button(
                            "Analyze Governance Health",
                            variant="primary",
                            elem_classes=["gr-button-primary"],
                        )

                    dash_summary = gr.Markdown(
                        "Enter a dataset and click 'Analyze' to view the current governance state."
                    )

                with gr.Row():
                    with gr.Column(elem_classes=["gcp-metric-card"]):
                        desc_metric = gr.HTML(
                            "<div class='gcp-metric-value'>-</div><div class='gcp-metric-label'>Description Gaps</div>"
                        )
                    with gr.Column(elem_classes=["gcp-metric-card"]):
                        gloss_metric = gr.HTML(
                            "<div class='gcp-metric-value'>-</div><div class='gcp-metric-label'>Glossary Gaps</div>"
                        )
                    with gr.Column(elem_classes=["gcp-metric-card"]):
                        orphan_metric = gr.HTML(
                            "<div class='gcp-metric-value'>-</div><div class='gcp-metric-label'>Orphaned Columns</div>"
                        )

                with gr.Row():
                    with gr.Column(elem_classes=["gcp-card"]):
                        gr.Markdown("### 🔍 Technical Description Gaps")
                        gr.Markdown(
                            "<small>Tables and columns lacking a documented description.</small>"
                        )
                        desc_output = gr.Dataframe(
                            headers=["Table", "Missing Descriptions"],
                            interactive=False,
                            wrap=True,
                        )
                    with gr.Column(elem_classes=["gcp-card"]):
                        gr.Markdown("### 📖 Business Glossary Gaps")
                        gr.Markdown(
                            "<small>Columns missing direct mapping to business glossary terms.</small>"
                        )
                        glossary_gap_output = gr.Dataframe(
                            headers=["Table", "Missing Glossary Mappings"],
                            interactive=False,
                            wrap=True,
                        )
                    with gr.Column(elem_classes=["gcp-card"]):
                        gr.Markdown("### ⚠️ Orphaned Assets")
                        gr.Markdown(
                            "<small>Columns missing both description and glossary mapping.</small>"
                        )
                        orphan_output = gr.Dataframe(
                            headers=["Table", "Orphaned Columns"],
                            interactive=False,
                            wrap=True,
                        )

                def dashboard_scan_wrapper(
                    project, loc, ds, cache_ds, cache_tbl, request: gr.Request
                ):
                    summary, d_agg, g_agg, o_agg, d_cnt, g_cnt, o_cnt = (
                        scan_dataset(
                            project, loc, ds, cache_ds, cache_tbl, request
                        )
                    )

                    d_html = f"<div class='gcp-metric-value'>{d_cnt}</div><div class='gcp-metric-label'>Description Gaps</div>"
                    g_html = f"<div class='gcp-metric-value'>{g_cnt}</div><div class='gcp-metric-label'>Glossary Gaps</div>"
                    o_html = f"<div class='gcp-metric-value'>{o_cnt}</div><div class='gcp-metric-label'>Orphaned Columns</div>"

                    return summary, d_agg, g_agg, o_agg, d_html, g_html, o_html

                scan_btn.click(
                    dashboard_scan_wrapper,
                    inputs=[
                        config_project,
                        config_location,
                        global_dataset,
                        config_cache_dataset,
                        config_cache_table,
                    ],
                    outputs=[
                        dash_summary,
                        desc_output,
                        glossary_gap_output,
                        orphan_output,
                        desc_metric,
                        gloss_metric,
                        orphan_metric,
                    ],
                )

            with gr.TabItem("Description Propagation"):
                with gr.Column(elem_classes=["gcp-card"]):
                    gr.Markdown("## 🧬 Analyze & Propagate Descriptions")
                    with gr.Row():
                        prop_table = gr.Textbox(
                            label="Target Table", value="transactions"
                        )

                    preview_btn = gr.Button(
                        "Analyze & Preview Description Propagation",
                        variant="primary",
                        elem_classes=["gr-button-primary"],
                    )

                    summary_output = gr.Markdown(
                        "Enter a table and click the button above to start analysis."
                    )
                    gr.Markdown(
                        "*(Optional: Click any cell in the **Proposed Description** column to refine it before applying)*"
                    )
                    preview_output = gr.Dataframe(
                        label="Propagation Candidates",
                        interactive=True,
                        wrap=True,
                        datatype=[
                            "bool",
                            "str",
                            "str",
                            "str",
                            "number",
                            "str",
                            "str",
                        ],
                    )

                    with gr.Row():
                        select_all_lineage_btn = gr.Button(
                            "Select All",
                            size="sm",
                            elem_classes=["gr-button-secondary"],
                        )
                        deselect_all_lineage_btn = gr.Button(
                            "Deselect All",
                            size="sm",
                            elem_classes=["gr-button-secondary"],
                        )

                    with gr.Row():
                        apply_btn = gr.Button(
                            "Apply Selection to BigQuery",
                            variant="primary",
                            elem_classes=["gr-button-primary"],
                        )

                    apply_result = gr.Textbox(
                        label="Apply Status", interactive=False
                    )

                select_all_lineage_btn.click(
                    select_all_lineage,
                    inputs=[preview_output],
                    outputs=[preview_output],
                )
                deselect_all_lineage_btn.click(
                    deselect_all_lineage,
                    inputs=[preview_output],
                    outputs=[preview_output],
                )

                preview_btn.click(lambda: "", outputs=[apply_result]).then(
                    analyze_and_preview,
                    inputs=[
                        config_project,
                        config_location,
                        global_dataset,
                        prop_table,
                    ],
                    outputs=[summary_output, preview_output],
                )
                apply_btn.click(
                    apply_propagation_improved,
                    inputs=[
                        config_project,
                        config_location,
                        global_dataset,
                        prop_table,
                        preview_output,
                    ],
                    outputs=apply_result,
                )

            with gr.TabItem("Glossary Recommendations"):
                with gr.Column(elem_classes=["gcp-card"]):
                    gr.Markdown("## 📖 Business Glossary Mapping")
                    gr.Markdown(
                        "Recommends mappings of columns to business glossary terms across tables."
                    )

                    with gr.Row():
                        glossary_table = gr.Textbox(
                            label="Target Table", value="customers"
                        )
                        confidence_slider = gr.Slider(
                            minimum=0.0,
                            maximum=1.0,
                            value=0.5,
                            step=0.05,
                            label="Minimum Confidence Threshold",
                            info="Filter out weak recommendations",
                        )

                    recommend_btn = gr.Button(
                        "Get Glossary Recommendations",
                        variant="primary",
                        elem_classes=["gr-button-primary"],
                    )

                with gr.Column(elem_classes=["gcp-card"]):
                    recommendations_view = gr.Dataframe(
                        label="Glossary Recommendations",
                        interactive=True,
                        wrap=True,
                        datatype=["bool", "str", "str", "number", "str", "str"],
                    )

                    with gr.Row():
                        select_all_glossary_btn = gr.Button(
                            "Select All",
                            size="sm",
                            elem_classes=["gr-button-secondary"],
                        )
                        deselect_all_glossary_btn = gr.Button(
                            "Deselect All",
                            size="sm",
                            elem_classes=["gr-button-secondary"],
                        )

                    with gr.Row():
                        apply_glossary_btn = gr.Button(
                            "Apply Selected Terms to Knowledge Catalog",
                            variant="primary",
                            elem_classes=["gr-button-primary"],
                        )

                    glossary_apply_result = gr.Textbox(
                        label="Apply Status", interactive=False
                    )

                select_all_glossary_btn.click(
                    select_all_glossary,
                    inputs=[recommendations_view],
                    outputs=[recommendations_view],
                )
                deselect_all_glossary_btn.click(
                    deselect_all_glossary,
                    inputs=[recommendations_view],
                    outputs=[recommendations_view],
                )

                recommend_btn.click(
                    lambda: "", outputs=[glossary_apply_result]
                ).then(
                    get_glossary_recommendations,
                    inputs=[
                        config_project,
                        config_location,
                        global_dataset,
                        glossary_table,
                        confidence_slider,
                        config_cache_dataset,
                        config_cache_table,
                    ],
                    outputs=recommendations_view,
                )

                apply_glossary_btn.click(
                    apply_glossary_selections,
                    inputs=[
                        config_project,
                        config_location,
                        global_dataset,
                        glossary_table,
                        recommendations_view,
                        config_cache_dataset,
                        config_cache_table,
                    ],
                    outputs=glossary_apply_result,
                )

            with gr.TabItem("Policy Tag Propagation"):
                with gr.Column(elem_classes=["gcp-card"]):
                    gr.Markdown("## 🛡️ Policy Tag Propagation")
                    gr.Markdown(
                        "Recommends propagating policy tags based on lineage and transformation assessment."
                    )

                    with gr.Row():
                        policy_table = gr.Textbox(
                            label="Target Table", value="customers"
                        )

                    policy_recommend_btn = gr.Button(
                        "Get Policy Tag Recommendations",
                        variant="primary",
                        elem_classes=["gr-button-primary"],
                    )

                with gr.Column(elem_classes=["gcp-card"]):
                    policy_recommendations_view = gr.Dataframe(
                        label="Policy Tag Recommendations",
                        interactive=True,
                        wrap=True,
                        datatype=[
                            "bool",
                            "str",
                            "str",
                            "str",
                            "str",
                            "str",
                            "str",
                        ],
                    )

                    with gr.Row():
                        additional_readers_txt = gr.Textbox(
                            label="Additional Readers to add (Comma separated)",
                            placeholder="group:data-scientists@example.com, user:analyst@example.com",
                        )

                    with gr.Row():
                        select_all_policy_btn = gr.Button(
                            "Select All",
                            size="sm",
                            elem_classes=["gr-button-secondary"],
                        )
                        deselect_all_policy_btn = gr.Button(
                            "Deselect All",
                            size="sm",
                            elem_classes=["gr-button-secondary"],
                        )

                    with gr.Row():
                        apply_policy_btn = gr.Button(
                            "Apply Selected Tags to BigQuery",
                            variant="primary",
                            elem_classes=["gr-button-primary"],
                        )

                    policy_apply_result = gr.Textbox(
                        label="Apply Status", interactive=False
                    )

                select_all_policy_btn.click(
                    select_all_policy,
                    inputs=[policy_recommendations_view],
                    outputs=[policy_recommendations_view],
                )
                deselect_all_policy_btn.click(
                    deselect_all_policy,
                    inputs=[policy_recommendations_view],
                    outputs=[policy_recommendations_view],
                )

                policy_recommend_btn.click(
                    lambda: "", outputs=[policy_apply_result]
                ).then(
                    get_policy_tag_recommendations,
                    inputs=[
                        config_project,
                        config_location,
                        global_dataset,
                        policy_table,
                    ],
                    outputs=policy_recommendations_view,
                )

                apply_policy_btn.click(
                    apply_policy_tag_recommendations,
                    inputs=[
                        config_project,
                        config_location,
                        global_dataset,
                        policy_table,
                        policy_recommendations_view,
                        additional_readers_txt,
                    ],
                    outputs=policy_apply_result,
                )

            with gr.TabItem("Trust Center (DQ)"):
                with gr.Column(elem_classes=["gcp-card"]):
                    gr.Markdown("## 💎 Data Trust & Quality Propagation")
                    gr.Markdown(
                        "Visualizes derived trust scores for tables and views based on upstream quality and transformation logic."
                    )

                    with gr.Row():
                        dq_table = gr.Textbox(
                            label="Target Table/View", value="transactions"
                        )

                    dq_analyze_btn = gr.Button(
                        "Analyze Trust & Quality",
                        variant="primary",
                        elem_classes=["gr-button-primary"],
                    )

                with gr.Column(elem_classes=["gcp-card"]):
                    dq_results_view = gr.Dataframe(
                        label="Column Trust Metrics",
                        interactive=False,
                        wrap=True,
                    )

                    gr.Markdown("### 💡 Trust Logic")
                    gr.Markdown(
                        "- **Conservative Scoring**: Minimum quality of all upstream contributors.\n- **Remediation Bonus**: Automatic detection of `DISTINCT` or `COALESCE` improves the derived score.\n- **Trend Analysis**: Compares current score against the last 5 snapshots."
                    )

                dq_analyze_btn.click(
                    get_dq_propagation,
                    inputs=[
                        config_project,
                        config_location,
                        global_dataset,
                        dq_table,
                    ],
                    outputs=dq_results_view,
                )

    # Helper to check auth status
    def check_auth_status(request: gr.Request):
        client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
        client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")

        # Bypass OAuth if client ID or secret are missing, placeholders, or if requested via env var
        if (
            not client_id
            or not client_secret
            or "YOUR_CLIENT_ID" in client_id
            or os.environ.get("BYPASS_OAUTH") == "true"
        ):
            logger.info(
                "Bypassing Google OAuth login screen (running in local ADC mode)."
            )
            return gr.update(visible=False), gr.update(visible=True)

        if request and "google_token" in request.session:
            return gr.update(visible=False), gr.update(visible=True)
        return gr.update(visible=True), gr.update(visible=False)

    demo.load(check_auth_status, outputs=[login_view, app_view])

if __name__ == "__main__":
    from fastapi import FastAPI

    main_app = FastAPI()

    main_app.add_middleware(
        SessionMiddleware,
        secret_key="some-secret-key-for-auth-propagation",
        session_cookie="steward_session",
    )

    @main_app.get("/google_login")
    async def login(request: fastapi.Request):
        redirect_uri = os.environ.get(
            "GOOGLE_REDIRECT_URI", "http://localhost:7860/google_callback"
        )
        client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
        logger.info(
            f"Initiating login: client_id={client_id[:10]}... redirect_uri={redirect_uri}"
        )
        return await oauth_config.google.authorize_redirect(
            request, redirect_uri
        )

    @main_app.get("/google_callback")
    async def auth_callback(request: fastapi.Request):
        try:
            token = await oauth_config.google.authorize_access_token(request)
            request.session["google_token"] = token
            logger.info("Successfully received token and stored in session.")
            return RedirectResponse(url="/")
        except Exception as e:
            logger.error(f"Auth callback failed: {e}")
            return RedirectResponse(url="/?error=auth_failed")

    @main_app.get("/logout")
    async def logout(request: fastapi.Request):
        request.session.pop("google_token", None)
        return RedirectResponse(url="/")

    app = gr.mount_gradio_app(main_app, demo, path="/")

    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 7860))
    uvicorn.run(app, host=host, port=port)

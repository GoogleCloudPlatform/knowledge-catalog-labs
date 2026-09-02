# Use the official Python 3.11 slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies required for compilation and git operations
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy project metadata and dependencies
COPY pyproject.toml README.md ./

# Copy source code and resources
COPY metadata_propagation/ ./metadata_propagation/
COPY glossary_terms.json knowledge_insights.json ./
COPY unstructured_docs/ ./unstructured_docs/

# Install the application and all dependencies
RUN pip install --no-cache-dir .

# Cloud Run defaults: listens on port 8080 (passed via PORT env var)
ENV PORT=8080
ENV HOST=0.0.0.0
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

# Launch the Gradio app via module entrypoint
CMD ["python3", "-m", "metadata_propagation.ui.gradio_app"]

FROM python:3.13-slim
WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev
COPY app ./app
COPY migrations ./migrations
COPY scripts ./scripts
ENV PATH="/srv/.venv/bin:$PATH"
EXPOSE 8000

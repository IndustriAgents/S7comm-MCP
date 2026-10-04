FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy

WORKDIR /app

RUN pip install --no-cache-dir uv

# Install exactly what uv.lock pins. The python-snap7 2.x wheel bundles the
# Snap7 native library, so no system packages are needed. Do not replace this
# with an unpinned pip install: the python-snap7 3.x server serves empty areas
# and the mock's memory map reads back as zeros.
COPY pyproject.toml uv.lock README.md s7_mock_server.py ./
RUN uv sync --locked --no-dev

EXPOSE 1102

CMD ["uv", "run", "--no-sync", "s7comm-mock-server"]

FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY bulb_bridge ./bulb_bridge
COPY simulators ./simulators
RUN pip install --no-cache-dir . && useradd --system --no-create-home app

USER app
EXPOSE 8000
CMD ["uvicorn", "--factory", "bulb_bridge.main:create_app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.14-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["rasptele"]

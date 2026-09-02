FROM python:3.12-slim

WORKDIR /app

# Le dipendenze prima del codice: cambiano di rado, e il layer si riusa.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --prefer-binary ".[server]"

COPY seed.py ./
RUN mkdir -p data

CMD ["uvicorn", "catena.server.main:app", "--host", "0.0.0.0", "--port", "8021"]

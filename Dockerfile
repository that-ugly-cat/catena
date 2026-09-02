FROM python:3.12-slim

WORKDIR /app

# Dependencies before the code: they change rarely, so the layer gets reused.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --prefer-binary ".[server]"

# Both operator scripts have to be inside the image: DEPLOY.md reaches them
# with `docker exec`, and link_borant.py in particular is needed exactly when
# something has gone wrong and nobody wants to be copying files around.
COPY seed.py link_borant.py ./
RUN mkdir -p data

CMD ["uvicorn", "catena.server.main:app", "--host", "0.0.0.0", "--port", "8022"]

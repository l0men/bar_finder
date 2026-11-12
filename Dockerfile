FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

WORKDIR /app

# Copier les fichiers de dépendances uv
COPY pyproject.toml uv.lock ./

# Installer les dépendances dans l'environnement virtuel
RUN uv sync --frozen --no-dev

# Copier le code, l'interface et le dossier data/ (qui contient déjà tes JSON)
COPY src/ ./src/
COPY static/ ./static/
COPY data/ ./data/

# Exposer le port (7860 par défaut, ou la variable $PORT fournie par Render)
EXPOSE 7860

CMD ["sh", "-c", "uv run uvicorn src.app:app --host 0.0.0.0 --port ${PORT:-7860}"]
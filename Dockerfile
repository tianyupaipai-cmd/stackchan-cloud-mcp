FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HUB_DISABLE_XET=1

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg libopus0 nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY deploy/requirements.txt /app/deploy/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r deploy/requirements.txt

COPY cloud /app/cloud
COPY patches /app/patches
COPY sprites /app/sprites
RUN PKG="$(python -c 'import stackchan_mcp, pathlib; print(pathlib.Path(stackchan_mcp.__file__).parent)')" \
      bash patches/apply_patches.sh \
    && node sprites/gen_avatar_v3.js \
    && test "$(wc -c < sprites/avatar_set.bin)" -eq 1536000 \
    && useradd --create-home --uid 10001 stackchan \
    && chown -R stackchan:stackchan /app

USER stackchan
EXPOSE 8080
CMD ["python", "-m", "cloud.railway_server"]

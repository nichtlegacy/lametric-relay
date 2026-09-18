# The icons come from the LaMetric community library and are not stored in the repo.
# This stage fetches them at build time; ImageMagick stays here, out of the runtime image.
FROM python:3.13-alpine AS icons
RUN apk add --no-cache imagemagick
WORKDIR /build
COPY assets/icons.json ./assets/icons.json
COPY scripts/icon.py scripts/fetch-icons.py ./scripts/
RUN python scripts/fetch-icons.py

FROM python:3.13-alpine
WORKDIR /app
COPY hub.py lametric.py quota.py names.py version.py ./
COPY addins/ ./addins/
COPY assets/ ./assets/
COPY --from=icons /build/assets/ ./assets/
# Stdlib only: no pip or ImageMagick at runtime. scripts/ contains development-only
# tools and does not belong in the runtime image.
# Without PYTHONUNBUFFERED, Python buffers stdout when no terminal is attached and
# `docker compose logs` would remain empty for minutes.
ENV PYTHONUNBUFFERED=1

EXPOSE 8099
CMD ["./hub.py"]

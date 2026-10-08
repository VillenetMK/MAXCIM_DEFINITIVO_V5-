FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY packages ./packages
COPY backend ./backend
COPY frontend ./frontend
RUN pip install --no-cache-dir -e . && useradd --uid 1000 --create-home maxcim && mkdir -p /data && chown maxcim /data
USER maxcim
ENV MAXCIM_HOST=0.0.0.0 MAXCIM_DATABASE=/data/team.db MAXCIM_MODE=simulation
EXPOSE 8080
CMD ["python", "-m", "maxcim_api.server"]

FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py mail_provider.py ./
COPY fixtures/ ./fixtures/
RUN useradd --create-home appuser
USER appuser
ENV HOST=0.0.0.0 PORT=8080 HANLUWORK_MODE=mock
EXPOSE 8080
CMD ["python", "server.py"]

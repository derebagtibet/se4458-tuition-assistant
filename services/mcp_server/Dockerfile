FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MCP_PORT=8001
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY policy_search ./policy_search
COPY server.py .
COPY data/University_Tuition_Policies.md ./data/
# Index the document once, at build time (not per question).
RUN python -m policy_search.indexer build data/University_Tuition_Policies.md data/policy_index.json
EXPOSE 8001
USER nobody
CMD ["python", "server.py"]

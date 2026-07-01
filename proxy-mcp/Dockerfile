# SAP SRE command proxy — MCP server
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .

# Streamable-HTTP MCP server. The default FastMCP port is 8000.
EXPOSE 8000
ENV SUBSCRIPTION_ID=""
CMD ["python", "server.py"]

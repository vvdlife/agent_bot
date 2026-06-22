#!/bin/bash

echo "==================================================="
echo "[Personal AI Agent] Stopping Docker Containers..."
echo "==================================================="
echo ""

if command -v docker-compose &> /dev/null; then
    docker-compose down
else
    docker compose down
fi

if [ $? -ne 0 ]; then
    echo ""
    echo "[ERROR] Failed to stop Docker containers."
    exit 1
fi

echo ""
echo "==================================================="
echo "[SUCCESS] Containers stopped successfully!"
echo "==================================================="

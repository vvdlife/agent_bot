#!/bin/bash

echo "==================================================="
echo "[Personal AI Agent] Starting Docker Containers..."
echo "==================================================="
echo ""

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "[ERROR] Docker is not installed or not in PATH."
    echo "Please install Docker and try again."
    exit 1
fi

# Run docker-compose up
echo "Running 'docker compose up -d --build'..."
if command -v docker-compose &> /dev/null; then
    docker-compose up -d --build
else
    docker compose up -d --build
fi

if [ $? -ne 0 ]; then
    echo ""
    echo "[ERROR] Failed to start Docker containers."
    echo "Please check if Docker service is running and try again."
    exit 1
fi

echo ""
echo "==================================================="
echo "[SUCCESS] Containers started successfully!"
echo ""
echo "Starting bot logs streaming..."
echo "(Press Ctrl+C at any time to stop viewing logs)"
echo "==================================================="
echo ""

if command -v docker-compose &> /dev/null; then
    docker-compose logs -f
else
    docker compose logs -f
fi

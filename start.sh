#!/bin/bash

# Start script for Translation Management System

echo "🌍 Starting Translation Management System..."

# Check if .env file exists
if [ ! -f .env ]; then
    echo "❌ Error: .env file not found"
    echo "📝 Please copy .env.example to .env and configure your settings"
    exit 1
fi

# Check if Docker is installed
if command -v docker-compose &> /dev/null; then
    echo "🐳 Using Docker Compose..."
    docker-compose up --build
else
    echo "⚠️  Docker Compose not found. Starting services manually..."

    # Start backend
    echo "🔧 Starting backend..."
    cd backend
    python3 -m pip install -r requirements.txt > /dev/null 2>&1
    python3 -m uvicorn main:app --reload --port 8000 &
    BACKEND_PID=$!
    cd ..

    # Start frontend
    echo "⚛️  Starting frontend..."
    cd frontend
    npm install > /dev/null 2>&1
    npm run dev &
    FRONTEND_PID=$!
    cd ..

    echo "✅ Services started!"
    echo "   Frontend: http://localhost:3000"
    echo "   Backend: http://localhost:8000"
    echo "   API Docs: http://localhost:8000/docs"
    echo ""
    echo "Press Ctrl+C to stop all services"

    # Trap Ctrl+C and kill processes
    trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT

    # Wait for processes
    wait
fi

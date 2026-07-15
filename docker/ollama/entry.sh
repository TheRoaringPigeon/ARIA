#!/bin/sh

echo "Starting Ollama server in background..."
ollama serve &
OLLAMA_PID=$!

echo "Waiting for Ollama server to be ready..."
until ollama list >/dev/null 2>&1; do
  echo "Still waiting for Ollama..."
  sleep 1
done

echo "Ollama is ready."

echo "Checking for $OLLAMA_MODEL model..."

if ! ollama list | grep -q "$OLLAMA_MODEL"; then
  echo "Pulling $OLLAMA_MODEL model..."
  ollama pull "$OLLAMA_MODEL"
else
  echo "$OLLAMA_MODEL model already present."
fi

echo "Ready. Keeping Ollama server in foreground..."
wait "$OLLAMA_PID"

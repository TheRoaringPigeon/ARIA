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

for MODEL in "$OLLAMA_MODEL" "$EMBED_MODEL"; do
  echo "Checking for $MODEL model..."
  if ! ollama list | grep -q "$MODEL"; then
    echo "Pulling $MODEL model..."
    ollama pull "$MODEL"
  else
    echo "$MODEL model already present."
  fi
done

echo "Ready. Keeping Ollama server in foreground..."
wait "$OLLAMA_PID"

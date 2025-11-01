#!/bin/bash

OUTFILE="app/DB/schema.py"

# Get the Database URL from the .env file
if [ -f .env ]; then
  set -o allexport
  source .env
  set +o allexport

  if [ -n "$DATABASE_URL" ]; then
    echo "Using DATABASE_URL from .env"
  else
    echo "DATABASE_URL variable not set in .env"
  fi
else
  echo ".env file not found"
fi

# Generate the schema using sqlacodegen
sqlacodegen --generator declarative --outfile "$OUTFILE" "$DATABASE_URL"
if [ $? -eq 0 ]; then
  echo "Schema generated successfully and saved to $OUTFILE"
else
  echo "Failed to generate schema"
fi
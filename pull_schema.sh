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
  echo -e "\033[32m✅ Schema generated successfully and saved to $OUTFILE\033[0m"
else
  echo -e "\033[31m❌ Failed to generate schema\033[0m"
fi
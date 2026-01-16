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
  
  # Show the differences summary
  echo ""
  if git diff --quiet "$OUTFILE" 2>/dev/null; then
    echo -e "\033[33m📊 No changes detected in schema\033[0m"
  else
    echo -e "\033[36m📊 Schema changes:\033[0m"
    git diff --stat "$OUTFILE" 2>/dev/null || echo -e "\033[33m⚠️  Git not available or file not tracked\033[0m"
  fi
else
  echo -e "\033[31m❌ Failed to generate schema\033[0m"
fi
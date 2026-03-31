# Environment Variables

This document explains how environment variables are loaded and managed in this project.

## Loading Order

Environment variables are loaded in the following order

1. **Infisical** (development only) - Secrets injected by Infisical CLI
2. **`.env.local`** - overrides Infisical

The Infisical secrets will be available as environment variables, which can then be overridden by your `.env.local` file.

## Infisical Setup

1. Install Infisical CLI: https://infisical.com/docs/cli/overview
2. Login: `infisical login`
3. Run the app (running the run.sh script automatically uses infisical to load secrets)

## Infisical Dashboard

you can see and manage the secrets in the [link here](https://infisical.albrrak773.com/organizations/de21a8c1-87e7-4f92-9e3b-253791905f8e/projects/secret-management/300b1e97-7e52-4a4e-872d-053b9082cac5/overview)

## Why This Order?

This setup allows:

- **Team-wide secrets** managed via Infisical (production DB URLs, API keys, etc.)
- **Developer customization** via `.env.local` for local development needs

## Required Environment Variables

this may change over time

| Variable                 | Description                                                                      |
| ------------------------ | -------------------------------------------------------------------------------- |
| `ENV`                  | Environment name (`development` or `production`). Defaults to `Production` |
| `DATABASE_URL`         | MySql connection URL                                                            |
| `CLERK_JWKS_URL`       | Clerk JWKS URL for authentication                                                |
| `CERTIFICATE_API_URL`  | Certificate generation API URL                                                   |
| `GOOGLE_CLIENT_ID`     | Google OAuth client ID                                                           |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret                                                       |
| `JWT_SECRET`           | Secret for JWT token signing                                                     |

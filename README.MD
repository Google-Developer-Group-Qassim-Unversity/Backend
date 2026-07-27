# Local Development
## Virtual Environment and Dependencies
This project uses [uv](https://docs.astral.sh/uv/) for package and project management. [install](https://docs.astral.sh/uv/#installation)

to create a virtual environment and install all the dependencies as defined in [pyproject.toml](./pyproject.toml)
```bash
uv sync
```

to see all dependencies including transitive ones:
```bash
uv tree
```

## Environment Varibles and Secret Management
This project uses [Infiscal](https://infisical.com/) on a self hosted instance at [infisical.albrrak773.com](https://infisical.albrrak773.com/)
to manage secrets.
**We do not use `.env` files!** [learn why](https://infisical.com/blog/stop-using-env-files)

Almost all commands that interact with the FastAPI server and other stuff like migrations or scripts 
use `infisical run` to get the environment varibles **as expected by [Settings](./config.py)**


## Running commands
we use poethepoet task manger to get an `npm run`-like experince over form JS land.

This is development dpendncy so you don't need to install it, running `uv sync` means you alrady have it

You can see all commands and their implementaion in [pyproject.toml](./pyproject.toml) under the `[tool.poe.tasks]` section

You can list all avalible commands by running
```bash
uv run poe 
```
### Run the FastAPI server
```bash
uv run poe dev
# runs 'fastapi dev' to start the server in dev mode
```

### Run Type Check, Lint and Format
```bash
uv run poe typecheck
uv run poe lint
uv run format 
```

### Create and Run Migrations
```bash
# create a new migration after app/models.py changes
uv run poe revision -m "describe change"

# run the migrataion
# > alembic upgrade head
uv run poe migrate 
```
    

# Local development requirements

* create a `.env` and add a DATABASE_URL varible

#### uv, Virtual environment and Dependencies

> This is technically optional, if you know what you are doing then do your thing otherwise follow these steps

To setup uv a venv and and install all the depedencies like fastAPI sqlalchemy and 50 others follow these steps

1. **Install `uv`** [official site](https://docs.astral.sh/uv/getting-started/installation/)
2. **Set up a vertual env**
   after installing uv and while in the root directory of the backend
   run `uv venv -p python3.13 .venv` ⚠️ don't change this command the specific version python3.13 is intentional
3. Install depedencies
   after setting up a .venv run `uv pip install -r requirements.txt`
   which will install all the required modules used by this server.
4. all done run the script `run.sh` to start the server, and go to http://localhost:7001/docs

## Automatically generate DB schema

use the sqlacodegen tool which can be installed with

```bash
pip install sqlacodegen mysqlclient
```

mysqlclient is a dependency for `sqlacodegen` to work

To generate the shcema run

```bash
sqlacodegen --generator declarative --outfile <path> <DATABASE_URL>
```

However This will not generate all the required code, you need to add `passive_deletes=True` so that ON CASCADE DELETE rules work properly

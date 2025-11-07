### Automatically generate DB schema

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

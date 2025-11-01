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

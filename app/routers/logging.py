from os import path
from sys import exc_info
from traceback import extract_tb, format_exception
from datetime import datetime
from pathlib import Path
from json import dump, dumps
from contextvars import ContextVar
from app.config import config

_current_log_file: ContextVar[Path | None] = ContextVar("log_file", default=None)


def create_log_file(end_point: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    file_path = Path(f"{config.LOG_DIR}/{end_point}/[{timestamp}]/messages.log")
    file_path.parent.mkdir(exist_ok=True, parents=True)
    file_path.touch()

    return file_path


def resolve_file(file: Path | None = None) -> Path:
    return file or _current_log_file.get()


class LogFile:
    def __init__(self, end_point: str):
        self._end_point = end_point
        self.file: Path | None = None

    def __enter__(self):
        self.file = create_log_file(self._end_point)
        self._token = _current_log_file.set(self.file)
        return self

    def __exit__(self, *exc):
        _current_log_file.reset(self._token)
        self.file = None


def write_log(file_or_message=None, message=None):
    if isinstance(file_or_message, Path):
        resolved = file_or_message
        actual_message = message or ""
    else:
        resolved = resolve_file()
        actual_message = file_or_message if file_or_message is not None else ""
    print(actual_message)
    with open(resolved, "a") as f:
        f.write(actual_message + "\n")

def write_file(file: Path, message: str):
    with open(file, "a") as f:
        f.write(message + "\n")

def write_log_title(file_or_title=None, title=None):
    if isinstance(file_or_title, Path):
        resolved = file_or_title
        actual_title = title or ""
    else:
        resolved = resolve_file()
        actual_title = file_or_title if file_or_title is not None else ""
    write_log(resolved, f"\033[34m{'-'*20}\033[0m[{actual_title}]\033[34m{'-'*20}\033[0m")
    write_log(resolved, "\n\033[33mLog\033[0m 🧾:")

def write_log_json(file_or_json=None, json=None):
    if isinstance(file_or_json, Path):
        resolved = file_or_json
        actual_json = json
    else:
        resolved = resolve_file()
        actual_json = file_or_json
    write_log(resolved, "\n\033[33mJSON Body\033[0m 📦:")
    json_file = resolved.with_name("body.json")
    
    with open(json_file, 'w', encoding='utf-8') as f:
        dump(actual_json, f, indent=4, ensure_ascii=False)
    print(dumps(actual_json, indent=4, ensure_ascii=False))

def Assert(file_or_condition=None, condition_or_message=None, message=None):
    if isinstance(file_or_condition, Path):
        resolved = file_or_condition
        actual_condition = condition_or_message
        actual_message = message
    else:
        resolved = resolve_file()
        actual_condition = file_or_condition if isinstance(file_or_condition, bool) else False
        actual_message = condition_or_message
    assert actual_message is not None, "Assert message must be provided"
    if not actual_condition:
        write_log(resolved, f"\n\033[31mAssertion Failed\033[0m ❌:\n{actual_message}")
        assert actual_condition, actual_message

def write_log_traceback(file=None):
    resolved = resolve_file(file)
    write_log(resolved, "\n\033[33msummrized traceback\033[0m 🗂️:")
    tb_file = resolved.with_name("traceback.log")
    tb = exc_info()[2]
    for frame in extract_tb(tb):
        f = f"...{path.sep.join(frame.filename.split(path.sep)[-3:])}, line {frame.lineno}:{frame.colno}"
        write_log(tb_file, f)

    exc_type, exc, _ = exc_info()
    full = "".join(format_exception(exc_type, exc, tb))
    write_file(tb_file, 3*'\n' + full)

def print_summarized_traceback() -> str:
    tb = exc_info()[2]
    print("\n\033[33msummrized traceback\033[0m 🗂️:\n")
    for frame in extract_tb(tb):
        print(f"...{path.sep.join(frame.filename.split(path.sep)[-3:])}, line {frame.lineno}:{frame.colno}\n")

def write_log_exception(file_or_err=None, err=None):
    if isinstance(file_or_err, Path):
        resolved = file_or_err
        actual_err = err
    else:
        resolved = resolve_file()
        actual_err = file_or_err
    if isinstance(actual_err, str):
        actual_err = Exception(actual_err)
    write_log(resolved, "\n\033[33mError\033[0m ❌:")
    err_file = resolved.with_name("error.log")
    write_log(err_file, str(actual_err))
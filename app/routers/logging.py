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


def _write_to(file: Path, message: str):
    print(message)
    with open(file, "a") as f:
        f.write(message + "\n")


def write_log(message: str):
    _write_to(_current_log_file.get(), message)

def write_log_to(file: Path, message: str):
    _write_to(file, message)

def write_file(file: Path, message: str):
    with open(file, "a") as f:
        f.write(message + "\n")

def write_log_title(title: str):
    file = _current_log_file.get()
    _write_to(file, f"\033[34m{'-'*20}\033[0m[{title}]\033[34m{'-'*20}\033[0m")
    _write_to(file, "\n\033[33mLog\033[0m 🧾:")

def write_log_title_to(file: Path, title: str):
    _write_to(file, f"\033[34m{'-'*20}\033[0m[{title}]\033[34m{'-'*20}\033[0m")
    _write_to(file, "\n\033[33mLog\033[0m 🧾:")

def write_log_json(json: str | dict):
    file = _current_log_file.get()
    _write_to(file, "\n\033[33mJSON Body\033[0m 📦:")
    json_file = file.with_name("body.json")
    with open(json_file, 'w', encoding='utf-8') as f:
        dump(json, f, indent=4, ensure_ascii=False)
    print(dumps(json, indent=4, ensure_ascii=False))

def write_log_json_to(file: Path, json: str | dict):
    _write_to(file, "\n\033[33mJSON Body\033[0m 📦:")
    json_file = file.with_name("body.json")
    with open(json_file, 'w', encoding='utf-8') as f:
        dump(json, f, indent=4, ensure_ascii=False)
    print(dumps(json, indent=4, ensure_ascii=False))

def Assert(condition: bool, message: str):
    assert message is not None, "Assert message must be provided"
    if not condition:
        _write_to(_current_log_file.get(), f"\n\033[31mAssertion Failed\033[0m ❌:\n{message}")
        assert condition, message

def write_log_traceback():
    file = _current_log_file.get()
    _write_to(file, "\n\033[33msummrized traceback\033[0m 🗂️:")
    tb_file = file.with_name("traceback.log")
    tb = exc_info()[2]
    for frame in extract_tb(tb):
        f = f"...{path.sep.join(frame.filename.split(path.sep)[-3:])}, line {frame.lineno}:{frame.colno}"
        _write_to(tb_file, f)

    exc_type, exc, _ = exc_info()
    full = "".join(format_exception(exc_type, exc, tb))
    write_file(tb_file, 3*'\n' + full)

def write_log_traceback_to(file: Path):
    _write_to(file, "\n\033[33msummrized traceback\033[0m 🗂️:")
    tb_file = file.with_name("traceback.log")
    tb = exc_info()[2]
    for frame in extract_tb(tb):
        f = f"...{path.sep.join(frame.filename.split(path.sep)[-3:])}, line {frame.lineno}:{frame.colno}"
        _write_to(tb_file, f)

    exc_type, exc, _ = exc_info()
    full = "".join(format_exception(exc_type, exc, tb))
    write_file(tb_file, 3*'\n' + full)

def print_summarized_traceback() -> str:
    tb = exc_info()[2]
    print("\n\033[33msummrized traceback\033[0m 🗂️:\n")
    for frame in extract_tb(tb):
        print(f"...{path.sep.join(frame.filename.split(path.sep)[-3:])}, line {frame.lineno}:{frame.colno}\n")

def write_log_exception(err: Exception | str):
    if isinstance(err, str):
        err = Exception(err)
    file = _current_log_file.get()
    _write_to(file, "\n\033[33mError\033[0m ❌:")
    err_file = file.with_name("error.log")
    _write_to(err_file, str(err))

def write_log_exception_to(file: Path, err: Exception | str):
    if isinstance(err, str):
        err = Exception(err)
    _write_to(file, "\n\033[33mError\033[0m ❌:")
    err_file = file.with_name("error.log")
    _write_to(err_file, str(err))

from os import path
from sys import exc_info
from traceback import extract_tb, format_exception
from datetime import datetime
from pathlib import Path
from json import dump, dumps
from pprint import pprint
from app.config import config


def create_log_file(end_point: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    file_path = Path(f"{config.LOG_DIR}/{end_point}/[{timestamp}]/messages.log")
    file_path.parent.mkdir(exist_ok=True, parents=True)
    file_path.touch()

    return file_path

def write_log(file: Path, message: str):
    print(message)
    with open(file, "a") as f:
        f.write(message + "\n")

def write_file(file: Path, message: str):
    with open(file, "a") as f:
        f.write(message + "\n")

def write_log_title(file: Path, title: str):
    write_log(file, f"\033[34m{'-'*20}\033[0m[{title}]\033[34m{'-'*20}\033[0m")
    write_log(file, "\n\033[33mLog\033[0m 🧾:")

def write_log_json(file: Path, json: str):
    write_log(file, "\n\033[33mJSON Body\033[0m 📦:")
    file = file.with_name("body.json")
    
    with open(file, 'w', encoding='utf-8') as f:
        dump(json, f, indent=4, ensure_ascii=False)
    print(dumps(json, indent=4, ensure_ascii=False))

# Good helper... but doesn't work with linters (because they won't do type narrowing)
def Assert(file: Path, condition: bool, message: str):
    assert message is not None, "Assert message must be provided"
    if not condition:
        write_log(file, f"\n\033[31mAssertion Failed\033[0m ❌:\n{message}")
        assert condition, message

def write_log_traceback(file: Path):
    write_log(file, "\n\033[33msummrized traceback\033[0m 🗂️:")
    file = file.with_name("traceback.log")
    tb = exc_info()[2]
    for frame in extract_tb(tb):
        f = f"...{path.sep.join(frame.filename.split(path.sep)[-3:])}, line {frame.lineno}:{frame.colno}"
        write_log(file, f)

    exc_type, exc, _ = exc_info()
    full = "".join(format_exception(exc_type, exc, tb))
    write_file(file, 3*'\n' + full)

def print_summarized_traceback() -> str:
    tb = exc_info()[2]
    for frame in extract_tb(tb):
        print(f"...{path.sep.join(frame.filename.split(path.sep)[-3:])}, line {frame.lineno}:{frame.colno}\n")

def write_log_exception(file: Path, err: Exception):
    write_log(file, "\n\033[33mError\033[0m ❌:")
    file = file.with_name("error.log")
    write_log(file, str(err))
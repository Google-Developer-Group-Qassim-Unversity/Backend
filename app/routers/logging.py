from os import makedirs, path
from sys import exc_info
import traceback
# Logging stuff

makedirs("event_logs", exist_ok=True)
def write_log(file: str, message: str):
    try:
        with open(f"event_logs/{file}", 'x') as f:
            pass
    except FileExistsError:
        pass

    print(message)
    with open(f"event_logs/{file}", "a") as f:
        f.write(message + "\n")

def summarized_traceback(log_file: str):
    tb = exc_info()[2]
    for frame in traceback.extract_tb(tb):
        write_log(log_file, f"...{path.sep.join(frame.filename.split(path.sep)[-3:])}, line {frame.lineno}:{frame.colno}")
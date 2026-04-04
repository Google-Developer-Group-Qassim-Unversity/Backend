import shutil
import subprocess
import sys

COLUMNS = 120
SORT_BY = "lines"
OUTPUT_FORMAT = "commas"

EXCLUDED_PATHS = ["tests", "alembic", "*.ini", "scripts"]

INSTALL_INSTRUCTIONS = """
tokei is not installed. Install it with one of:

  Arch:     pacman -S tokei
  Ubuntu:   sudo apt install tokei
  macOS:    brew install tokei
  Windows:  winget install XAMPPRocky.tokei

See: https://github.com/XAMPPRocky/tokei#installation
""".strip()


def main():
    if not shutil.which("tokei"):
        print(INSTALL_INSTRUCTIONS, file=sys.stderr)
        sys.exit(1)

    subprocess.run(
        [
            "tokei",
            "-c",
            str(COLUMNS),
            "--files",
            "-s",
            SORT_BY,
            "-n",
            OUTPUT_FORMAT,
            *[f"-e{p}" for p in EXCLUDED_PATHS],
        ]
    )


if __name__ == "__main__":
    main()

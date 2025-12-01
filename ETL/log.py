"""
FastAPI Middleware for POST Request Logging + Error/Traceback Logging

This module provides:
1. Middleware that intercepts all POST requests and logs them
2. Standalone functions for logging errors and tracebacks

Directory structure: base_log_dir/endpoint_name/timestamp/
  - request.log: Full request information
  - body.json: Request body (prettified JSON)
  - error.log: Exception information
  - traceback.log: Full traceback
"""

import json
import traceback
from datetime import datetime
from pathlib import Path
from sys import exc_info
from typing import Callable
from os import path

from fastapi import Request, Response
import pendulum
from starlette.middleware.base import BaseHTTPMiddleware

BASE_LOG_DIR = "./logs"
# ═══════════════════════════════════════════════════════════════════════════
# ANSI Color Codes
# ═══════════════════════════════════════════════════════════════════════════

class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    GRAY = "\033[90m"


# ═══════════════════════════════════════════════════════════════════════════
# Core Utility Function - Single source of truth for all output
# ═══════════════════════════════════════════════════════════════════════════

def _output(
    content: str,
    filepath: Path = None,
    color: str = "",
    to_terminal: bool = False,
    to_file: bool = False,
) -> None:
    """
    Universal output utility for both file and terminal logging.
    
    This single function handles all logging to avoid code duplication.

    Args:
        content: The content to output
        filepath: Path to write to (if to_file=True)
        color: ANSI color code for terminal output
        to_terminal: Whether to output to terminal
        to_file: Whether to output to file
    """
    if to_terminal:
        print(f"{color}{content}{Colors.RESET}")

    if to_file and filepath:
        try:
            with open(filepath, "a" if filepath.exists() else "w", encoding="utf-8") as f:
                f.write(content + "\n")
        except Exception as e:
            print(f"{Colors.RED}Failed to write to {filepath}: {e}{Colors.RESET}")


# ═══════════════════════════════════════════════════════════════════════════
# Shared Directory Management
# ═══════════════════════════════════════════════════════════════════════════

def _get_log_directory(base_log_dir: str, endpoint: str) -> Path:
    """
    Get the log directory for a given endpoint at current timestamp.
    
    Args:
        base_log_dir: Base directory for all logs
        endpoint: The endpoint path (e.g., "/api/login")
        
    Returns:
        Path to the log directory for this endpoint and timestamp
    """
    timestamp = datetime.now().strftime("%d-%m-%Y %H:%M")
    endpoint_name = endpoint.strip("/").replace("/", "_") or "root"
    return Path(base_log_dir) / endpoint_name / timestamp


def _ensure_directory(directory: Path) -> bool:
    """
    Ensure directory exists, create if needed.
    
    Args:
        directory: Path to directory
        
    Returns:
        True if successful, False otherwise
    """
    try:
        directory.mkdir(parents=True, exist_ok=True)
        return True
    except Exception as e:
        _output(
            f"❌ Failed to create directory {directory}: {e}",
            color=Colors.RED + Colors.BOLD,
            to_terminal=True,
        )
        return False


# ═══════════════════════════════════════════════════════════════════════════
# Shared Formatting Utilities
# ═══════════════════════════════════════════════════════════════════════════

def _format_header(title: str, width: int = 80) -> list[str]:
    """Format a header section."""
    return [
        "=" * width,
        title,
        "=" * width,
        "",
    ]


def _format_section_header(title: str, width: int = 80) -> list[str]:
    """Format a subsection header."""
    return ["-" * width, title, "-" * width]


def _get_timestamp() -> str:
    """Get formatted timestamp."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ═══════════════════════════════════════════════════════════════════════════
# POST Request Logger Middleware
# ═══════════════════════════════════════════════════════════════════════════

class POSTRequestLoggerMiddleware(BaseHTTPMiddleware):
    """
    Middleware that logs all POST requests to filesystem and terminal.

    Args:
        app: FastAPI application instance
        base_log_dir: Base directory for all logs (default: "./logs")
    """

    def __init__(self, app, base_log_dir: str = BASE_LOG_DIR):
        super().__init__(app)
        self.base_log_dir = base_log_dir

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Intercept POST requests and log them before processing."""
        # Only process POST requests
        if request.method != "POST":
            return await call_next(request)

        # Read the request body (cached by Starlette for reuse)
        body = await request.body()

        # Generate folder path
        request_dir = _get_log_directory(self.base_log_dir, request.url.path)

        try:
            # Create directory structure
            if _ensure_directory(request_dir):
                # Parse body data
                body_data = self._parse_body(body)

                # Log to filesystem
                self._write_request_log(request_dir, request, body)
                self._write_body_json(request_dir, body_data)

                # Log to terminal
                self._log_to_terminal(request, request_dir, body_data)

        except Exception as e:
            # Gracefully handle errors without breaking the request
            _output(
                f"❌ Logging Error: {str(e)}",
                color=Colors.RED + Colors.BOLD,
                to_terminal=True,
            )

        # Continue with normal request processing
        response = await call_next(request)
        return response

    def _parse_body(self, body: bytes) -> dict | str:
        """Parse request body as JSON if possible, otherwise return as string."""
        if not body:
            return {}

        try:
            return json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return body.decode("utf-8", errors="replace")

    def _write_request_log(
        self, request_dir: Path, request: Request, body: bytes
    ) -> None:
        """Write comprehensive request log to file."""
        filepath = request_dir / "request.log"

        # Build complete log content
        sections = []
        sections.extend(_format_header("POST REQUEST LOG"))
        
        sections.append(f"Timestamp:    {_get_timestamp()}")
        sections.append(f"Endpoint:     {request.url.path}")
        sections.append(f"Method:       {request.method}")
        client = (
            f"{request.client.host}:{request.client.port}"
            if request.client
            else "Unknown"
        )
        sections.append(f"Client:       {client}")
        sections.append("")

        # Headers
        sections.extend(_format_section_header("HEADERS"))
        for key, value in request.headers.items():
            sections.append(f"{key:30} : {value}")
        sections.append("")

        # Query parameters
        sections.extend(_format_section_header("QUERY PARAMETERS"))
        if request.query_params:
            for key, value in request.query_params.items():
                sections.append(f"{key:30} : {value}")
        else:
            sections.append("No query parameters")
        sections.append("")

        # Body info
        sections.extend(_format_section_header("BODY INFO"))
        sections.append(f"Content-Length: {len(body)} bytes")
        sections.append(
            f"Content-Type:   {request.headers.get('content-type', 'Not specified')}"
        )
        sections.append("")
        sections.append("=" * 80)

        content = "\n".join(sections)

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            _output(
                f"Failed to write request.log: {e}",
                color=Colors.RED,
                to_terminal=True,
            )

    def _write_body_json(self, request_dir: Path, body_data: dict | str) -> None:
        """Write request body to JSON file."""
        filepath = request_dir / "body.json"

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                if isinstance(body_data, dict):
                    json.dump(body_data, f, indent=2, ensure_ascii=False)
                else:
                    f.write(body_data)
        except Exception as e:
            _output(
                f"Failed to write body.json: {e}",
                color=Colors.RED,
                to_terminal=True,
            )

    def _log_to_terminal(
        self, request: Request, request_dir: Path, body_data: dict | str
    ) -> None:
        """Output beautifully formatted and colored log to terminal."""
        # Header
        _output(
            f"\n{'=' * 80}",
            color=Colors.CYAN + Colors.BOLD,
            to_terminal=True,
        )
        _output(
            "POST REQUEST INTERCEPTED",
            color=Colors.CYAN + Colors.BOLD,
            to_terminal=True,
        )
        _output(
            f"{'=' * 80}",
            color=Colors.CYAN + Colors.BOLD,
            to_terminal=True,
        )

        # Basic information
        print(f"{Colors.GREEN}⏰ Timestamp:{Colors.RESET}    {_get_timestamp()}")
        print(
            f"{Colors.GREEN}🎯 Endpoint:{Colors.RESET}     "
            f"{Colors.YELLOW}{request.url.path}{Colors.RESET}"
        )
        print(
            f"{Colors.GREEN}📁 Log Folder:{Colors.RESET}   "
            f"{Colors.BLUE}{request_dir.relative_to(self.base_log_dir)}{Colors.RESET}"
        )

        if request.client:
            print(
                f"{Colors.GREEN}🌐 Client:{Colors.RESET}      "
                f"{request.client.host}:{request.client.port}"
            )

        # Headers preview (first 5)
        _output(
            "\n📋 Headers:",
            color=Colors.MAGENTA + Colors.BOLD,
            to_terminal=True,
        )
        header_items = list(request.headers.items())
        for key, value in header_items[:5]:
            print(f"  {Colors.GRAY}{key:25}{Colors.RESET} : {value[:60]}")
        if len(header_items) > 5:
            _output(
                f"  ... and {len(header_items) - 5} more",
                color=Colors.GRAY,
                to_terminal=True,
            )

        # Body preview (first 10 lines or 200 chars)
        _output(
            "\n📦 Body:",
            color=Colors.MAGENTA + Colors.BOLD,
            to_terminal=True,
        )
        if isinstance(body_data, dict):
            body_str = json.dumps(body_data, indent=2, ensure_ascii=False)
            lines = body_str.split("\n")
            for line in lines[:10]:
                _output(f"  {line}", color=Colors.GRAY, to_terminal=True)
            if len(lines) > 10:
                _output(
                    f"  ... ({len(lines) - 10} more lines)",
                    color=Colors.GRAY,
                    to_terminal=True,
                )
        else:
            preview = str(body_data)[:200]
            _output(f"  {preview}", color=Colors.GRAY, to_terminal=True)
            if len(str(body_data)) > 200:
                _output("  ... (truncated)", color=Colors.GRAY, to_terminal=True)

        # Footer
        _output(
            "\n✅ Logged successfully",
            color=Colors.GREEN + Colors.BOLD,
            to_terminal=True,
        )
        _output(
            f"{'=' * 80}\n",
            color=Colors.CYAN + Colors.BOLD,
            to_terminal=True,
        )


# ═══════════════════════════════════════════════════════════════════════════
# Error Logging Function
# ═══════════════════════════════════════════════════════════════════════════

def log_error(
    exception: Exception,
    endpoint: str,
    base_log_dir: str = BASE_LOG_DIR,
) -> None:
    """
    Log an exception to error.log with beautiful formatting.
    
    Args:
        exception: The exception object to log
        endpoint: The endpoint where the error occurred (e.g., "/api/login")
        base_log_dir: Base directory for logs
    """
    log_dir = _get_log_directory(base_log_dir, endpoint)
    
    if not _ensure_directory(log_dir):
        return
    
    filepath = log_dir / "error.log"
    
    # Build error log content
    sections = []
    sections.extend(_format_header("ERROR LOG"))
    sections.append(f"Timestamp:    {_get_timestamp()}")
    sections.append(f"Endpoint:     {endpoint}")
    sections.append(f"Exception:    {type(exception).__name__}")
    sections.append(f"Message:      {str(exception)}")
    sections.append("")
    sections.append("=" * 80)
    
    content = "\n".join(sections)
    
    # Write to file
    _output(content, filepath=filepath, to_file=True)
    
    # Terminal output with colors
    _output(
        f"\n{'=' * 80}",
        color=Colors.RED + Colors.BOLD,
        to_terminal=True,
    )
    _output(
        "ERROR LOGGED",
        color=Colors.RED + Colors.BOLD,
        to_terminal=True,
    )
    _output(
        f"{'=' * 80}",
        color=Colors.RED + Colors.BOLD,
        to_terminal=True,
    )
    
    print(f"{Colors.GREEN}⏰ Timestamp:{Colors.RESET}    {_get_timestamp()}")
    print(
        f"{Colors.GREEN}🎯 Endpoint:{Colors.RESET}     "
        f"{Colors.YELLOW}{endpoint}{Colors.RESET}"
    )
    print(
        f"{Colors.GREEN}❌ Exception:{Colors.RESET}    "
        f"{Colors.RED}{type(exception).__name__}{Colors.RESET}"
    )
    print(f"{Colors.GREEN}💬 Message:{Colors.RESET}     {str(exception)}")
    print(
        f"{Colors.GREEN}📁 Log File:{Colors.RESET}    "
        f"{Colors.BLUE}{filepath}{Colors.RESET}"
    )
    
    _output(
        "\n✅ Error logged successfully",
        color=Colors.GREEN + Colors.BOLD,
        to_terminal=True,
    )
    _output(
        f"{'=' * 80}\n",
        color=Colors.RED + Colors.BOLD,
        to_terminal=True,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Traceback Logging Function
# ═══════════════════════════════════════════════════════════════════════════

def log_traceback(
    endpoint: str,
    base_log_dir: str = BASE_LOG_DIR,
) -> None:
    """
    Log full traceback to traceback.log and print/log summarized version.
    
    This function gets the current exception info from sys.exc_info().
    
    Args:
        endpoint: The endpoint where the error occurred (e.g., "/api/login")
        base_log_dir: Base directory for logs
    """
    log_dir = _get_log_directory(base_log_dir, endpoint)
    
    if not _ensure_directory(log_dir):
        return
    
    # Get exception info
    exc_type, exc_value, tb = exc_info()
    
    if not tb:
        _output(
            "⚠️  No traceback available (no active exception)",
            color=Colors.YELLOW,
            to_terminal=True,
        )
        return
    
    # Full traceback for file
    full_filepath = log_dir / "traceback.log"
    full_traceback = "".join(traceback.format_exception(exc_type, exc_value, tb))
    
    sections = []
    sections.extend(_format_header("TRACEBACK LOG"))
    sections.append("")
    sections.extend(_format_section_header("FULL TRACEBACK"))
    sections.append(full_traceback)
    sections.append("=" * 80)
    
    full_content = "\n".join(sections)
    _output(full_content, filepath=full_filepath, to_file=True)
    
    # Summarized traceback for terminal and log
    summary_filepath = log_dir / "traceback_summary.log"
    summary_lines = []
    
    for frame in traceback.extract_tb(tb):
        line = f"...{path.sep.join(frame.filename.split(path.sep)[-3:])}, line {frame.lineno}:{frame.colno}"
        summary_lines.append(line)
    
    summary_content = "\n".join(summary_lines)
    
    # Write summary to file
    summary_sections = []
    summary_sections.extend(_format_header("TRACEBACK SUMMARY"))
    summary_sections.append("")
    summary_sections.extend(_format_section_header("STACK TRACE"))
    summary_sections.append(summary_content)
    summary_sections.append("")
    summary_sections.append("=" * 80)
    
    _output("\n".join(summary_sections), filepath=summary_filepath, to_file=True)
    
    # Terminal output with colors
    _output(
        f"\n{'=' * 80}",
        color=Colors.RED + Colors.BOLD,
        to_terminal=True,
    )
    _output(
        "TRACEBACK LOGGED",
        color=Colors.RED + Colors.BOLD,
        to_terminal=True,
    )
    _output(
        f"{'=' * 80}",
        color=Colors.RED + Colors.BOLD,
        to_terminal=True,
    )
    print(f"{Colors.GREEN}💬 Message:{Colors.RESET}     {str(exc_value)}")
    
    _output(
        f"\n🔍 Stack Trace:",
        color=Colors.MAGENTA + Colors.BOLD,
        to_terminal=True,
    )
    for line in summary_lines:
        _output(f"  {line}", color=Colors.GRAY, to_terminal=True)
    
    _output(
        "\n✅ Traceback logged successfully",
        color=Colors.GREEN + Colors.BOLD,
        to_terminal=True,
    )
    _output(
        f"{'=' * 80}\n",
        color=Colors.RED + Colors.BOLD,
        to_terminal=True,
    )

def print_cache_miss(endpoint: str) -> None:
    print(f"\n[{pendulum.now().format('DD-MM-YYYY HH:mm')}] {endpoint} Cache: \033[31mMISS\033[0m")
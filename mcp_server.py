#!/usr/bin/env python3
"""
MCP Server for Desktop Automation (v0.1.5) - Complete Enhanced Version

This server implements the Model Context Protocol for direct integration
with environments like Claude Desktop. It combines all features from the
original asyncio version with Windows compatibility and enhanced robustness.

Features:
- All 10 tools from original version with asyncio.to_thread() implementation
- Selective async usage: async for tool execution, sync for I/O (test compatibility)
- Advanced logging configuration with environment variable support
- Robust error handling with specific exception types and MCP-compliant codes
- Windows-compatible hybrid architecture with optimized performance
- Python version checking and graceful cleanup
- Enhanced JSON-RPC implementation with fallback serialization
- npx_execute tool for running npx commands with security and execution configurations.
"""
import json
import sys
import os
from pathlib import Path
import base64
import io
import logging
import asyncio
import threading
import subprocess
import shutil # Für shutil.which
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Any, Dict, Optional, Set, List

# --- Path Manipulation ---
project_root_dir = Path(__file__).resolve().parent.parent
if str(project_root_dir) not in sys.path:
    sys.path.insert(0, str(project_root_dir))

# --- Module imports ---
try:
    from mcp import window, capture, vision
    from mcp.input import backend as input_backend
    from mcp.window import WindowNotFoundError, WindowOperationError, BBox
    from mcp.vision import VisionError, TemplateNotFoundError, DetectionError, Detection
    from mcp.logger import get_logger, setup_logging
    from PIL import Image
except ImportError as e:
    print(f"Import error: {e}", file=sys.stderr)
    sys.exit(1)

# --- Logging ---
log_level_server_str: str = os.environ.get('MCP_LOG_LEVEL', "INFO").upper()
log_file_server_str: str | None = os.environ.get('MCP_LOG_FILE')
log_file_path_server: Path | None = None
if log_file_server_str and log_file_server_str.strip():
    log_file_path_server = Path(log_file_server_str)
if not os.getenv('MCP_TEST_MODE'):
    try:
        setup_logging(level=log_level_server_str, log_file=log_file_path_server, force=True)
        logger = get_logger(__name__)
    except Exception as e_log_setup:
        logging.basicConfig(level=getattr(logging, log_level_server_str, logging.INFO))
        logger = logging.getLogger(__name__)
        logger.warning(f"MCP logger setup failed, using basic logging: {e_log_setup}")
else:
    logging.disable(logging.CRITICAL)
    logger = logging.getLogger(__name__)

SERVER_VERSION = "0.1.5"
if sys.version_info < (3, 8):
    sys.stderr.write("CRITICAL Error: CoreUI-MCP Server requires Python 3.8 or newer.\n")
    sys.exit(1)

# --- NPX Security and Execution Configuration ---
_NPX_CONFIG: Dict[str, Any] = {
    "use_allowlist": True,
    "allowed_packages": [],
    "blocked_command_parts": [
        "rm ", "del ", "format ", "sudo ", "su ", "mv ", "cp ",
        "chmod ", "chown ", "shutdown", "reboot",
        ">", "<", "|", "&", ";", "$", "..",
        "wget ", "curl ", "git clone", "npm install"
    ],
    "allow_package_versions_in_name": True,
    "execution_timeout_seconds": 300, # Standard-Timeout für npx-Befehle
    "default_env_vars": {} # Standard-Umgebungsvariablen für npx
}

def _load_npx_config(config_file_path: Path = project_root_dir / "config.json") -> None:
    """Lädt die NPX-Sicherheits- und Ausführungskonfiguration aus der config.json."""
    global _NPX_CONFIG
    default_config_values = _NPX_CONFIG.copy()

    if config_file_path.exists() and config_file_path.is_file():
        try:
            with open(config_file_path, 'r', encoding='utf-8') as f:
                user_config_full = json.load(f)
            user_npx_config = user_config_full.get("security", {}).get("npx_execution", {})
            
            _NPX_CONFIG["use_allowlist"] = user_npx_config.get("use_allowlist", default_config_values["use_allowlist"])
            _NPX_CONFIG["allowed_packages"] = user_npx_config.get("allowed_packages", default_config_values["allowed_packages"])
            _NPX_CONFIG["blocked_command_parts"] = user_npx_config.get("blocked_command_parts", default_config_values["blocked_command_parts"])
            _NPX_CONFIG["allow_package_versions_in_name"] = user_npx_config.get("allow_package_versions_in_name", default_config_values["allow_package_versions_in_name"])
            _NPX_CONFIG["execution_timeout_seconds"] = user_npx_config.get("execution_timeout_seconds", default_config_values["execution_timeout_seconds"])
            _NPX_CONFIG["default_env_vars"] = user_npx_config.get("default_env_vars", default_config_values["default_env_vars"])

            if not isinstance(_NPX_CONFIG["execution_timeout_seconds"], (int, float)) or _NPX_CONFIG["execution_timeout_seconds"] <= 0:
                logger.warning(f"Invalid 'execution_timeout_seconds' ({_NPX_CONFIG['execution_timeout_seconds']}), using default: {default_config_values['execution_timeout_seconds']}s.")
                _NPX_CONFIG["execution_timeout_seconds"] = default_config_values["execution_timeout_seconds"]
            
            if not isinstance(_NPX_CONFIG["default_env_vars"], dict):
                logger.warning(f"Invalid 'default_env_vars' (must be a dictionary), using empty default.")
                _NPX_CONFIG["default_env_vars"] = default_config_values["default_env_vars"]

            if not os.getenv('MCP_TEST_MODE'):
                logger.info(f"NPX config loaded from '{config_file_path}'. Timeout: {_NPX_CONFIG['execution_timeout_seconds']}s.")
                logger.debug(f"NPX Allowed Packages: {_NPX_CONFIG['allowed_packages']}")
                logger.debug(f"NPX Default Env Vars: {_NPX_CONFIG['default_env_vars']}")
                
        except (json.JSONDecodeError, Exception) as e: # Allgemeinere Fehlerbehandlung
            if not os.getenv('MCP_TEST_MODE'):
                logger.warning(f"Error loading or parsing NPX config from '{config_file_path}': {e}. Using secure defaults.")
            _NPX_CONFIG = default_config_values
    else:
        if not os.getenv('MCP_TEST_MODE'):
            logger.warning(f"NPX config file '{config_file_path}' not found. Using secure defaults.")
        _NPX_CONFIG = default_config_values

# --- Tool Definitions ---
TOOL_DEFINITIONS = [
    # ... (andere Tools) ...
    { "name": "list_windows", "description": "Lists all available and visible windows with titles, IDs, and bounding boxes.", "inputSchema": {"type": "object", "properties": {}}},
    { "name": "focus_window", "description": "Focuses/activates a window specified by its title.", "inputSchema": { "type": "object", "properties": { "title": {"type": "string", "description": "Window title (or part of it) to focus."}}, "required": ["title"]}},
    { "name": "screenshot_window", "description": "Takes a screenshot of a window (by title) as a base64 encoded PNG.", "inputSchema": { "type": "object", "properties": { "title": {"type": "string", "description": "Window title (or part of it) to capture."}}, "required": ["title"]}},
    { "name": "click_template_in_window", "description": "Finds a template image within a window and clicks its center.", "inputSchema": { "type": "object", "properties": { "window_title": {"type": "string", "description": "Title of the window to search in."}, "template_base64": {"type": "string", "description": "Base64 encoded PNG/JPEG template image."}, "threshold": {"type": "number", "description": "Match confidence (0.0-1.0). Default: 0.8", "default": 0.8}}, "required": ["window_title", "template_base64"]}},
    { "name": "mouse_move", "description": "Moves mouse to absolute screen coordinates (x, y).", "inputSchema": { "type": "object", "properties": { "x": {"type": "integer", "description": "Absolute X coordinate."}, "y": {"type": "integer", "description": "Absolute Y coordinate."}}, "required": ["x", "y"]}},
    { "name": "mouse_click", "description": "Performs a mouse click (press & release) at coordinates.", "inputSchema": { "type": "object", "properties": { "x": {"type": "integer", "description": "Absolute X coordinate."}, "y": {"type": "integer", "description": "Absolute Y coordinate."}, "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left", "description": "Button to click."}}, "required": ["x", "y"]}},
    { "name": "mouse_drag", "description": "Drags mouse from start to end with a button held.", "inputSchema": { "type": "object", "properties": { "start_x": {"type": "integer"}, "start_y": {"type": "integer"}, "end_x": {"type": "integer"}, "end_y": {"type": "integer"}, "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"}, "duration_s": {"type": "number", "default": 0.5, "description": "Drag duration in seconds."}}, "required": ["start_x", "start_y", "end_x", "end_y"]}},
    { "name": "mouse_scroll", "description": "Simulates mouse wheel scrolling.", "inputSchema": { "type": "object", "properties": { "dx": {"type": "integer", "default": 0, "description": "Horizontal scroll units (+right, -left)."}, "dy": {"type": "integer", "default": 0, "description": "Vertical scroll units (+down, -up)."}}}},
    { "name": "keyboard_type_text", "description": "Types the given text string.", "inputSchema": { "type": "object", "properties": { "text": {"type": "string", "description": "Text to type."}}, "required": ["text"]}},
    { "name": "keyboard_press_key", "description": "Simulates a full key press (down & up). Key spec can be character or special key name (platform backend dependent).", "inputSchema": { "type": "object", "properties": { "key_spec": {"type": "string", "description": "Key to press (e.g., 'a', 'enter', 'ctrl+c')."}}, "required": ["key_spec"]}},
    {
        "name": "npx_execute",
        "description": "Execute npm packages using npx. Requires Node.js/npm. Security and execution behavior configured via config.json.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "package": {
              "type": "string",
              "description": "Package name to execute (e.g., 'cowsay', '@angular/cli@latest')."
            },
            "args": {
              "type": "array",
              "items": { "type": "string" },
              "description": "Arguments to pass to the package.",
              "default": []
            },
            "workingDirectory": {
              "type": "string",
              "description": "Working directory for execution. Defaults to MCP server's CWD.",
              "default": "."
            },
            "env": { # NEU: Optionale Umgebungsvariablen für diesen spezifischen Aufruf
                "type": "object",
                "additionalProperties": {"type": "string"}, # Erlaubt beliebige String-Key-Value-Paare
                "description": "Optional environment variables for the npx process. Merged with default_env_vars from config.",
                "default": {}
            }
          },
          "required": ["package"]
        }
      }
]

# --- Thread Pool, Response, Initialize (bleiben größtenteils gleich) ---
PARALLEL_WORKERS = int(os.environ.get('MCP_PARALLEL_WORKERS', '0'))
executor: Optional[ThreadPoolExecutor] = None
active_futures: Set[Future] = set()
def init_parallel_processing(): # ... (wie zuvor)
    global executor
    if PARALLEL_WORKERS > 0:
        executor = ThreadPoolExecutor(max_workers=PARALLEL_WORKERS, thread_name_prefix="MCP-Worker")
        if not os.getenv('MCP_TEST_MODE'):
            logger.info(f"Parallel processing enabled with {PARALLEL_WORKERS} workers")
def cleanup_parallel_processing(): # ... (wie zuvor)
    global executor, active_futures
    if executor:
        for future in list(active_futures):
            if not future.done():
                future.cancel()
        executor.shutdown(timeout=5.0)
        executor = None
        active_futures.clear()
        if not os.getenv('MCP_TEST_MODE'):
            logger.info("Parallel processing cleanup complete")
def send_response(response: dict[str, Any]) -> None: # ... (wie zuvor)
    try:
        json_str = json.dumps(response)
        sys.stdout.write(json_str + "\n")
        sys.stdout.flush()
        if not os.getenv('MCP_TEST_MODE'):
            resp_id = response.get("id", "N/A")
            if "error" in response:
                logger.debug(f"Sent error response (ID: {resp_id}): {response['error'].get('message', 'Unknown error')}")
            else:
                result_summary = str(type(response.get("result"))) if "result" in response else "No result field"
                if isinstance(response.get("result"), dict):
                    result_summary = f"Result keys: {list(response['result'].keys())}"
                logger.debug(f"Sent response (ID: {resp_id}): {result_summary}")
    except TypeError as te:
        if not os.getenv('MCP_TEST_MODE'):
            logger.critical(f"JSON serialization error for response (ID: {response.get('id')}): {te}. Partial data: {str(response)[:200]}", exc_info=True)
        fb_err = {"jsonrpc": "2.0", "id": response.get("id"), "error": {"code": -32603, "message": "Internal error: Response serialization failed."}}
        try:
            sys.stdout.write(json.dumps(fb_err) + "\n")
            sys.stdout.flush()
        except Exception as e_fb:
            if not os.getenv('MCP_TEST_MODE'):
                logger.critical(f"Failed to send fallback JSON error: {e_fb}")
    except Exception as e:
        if not os.getenv('MCP_TEST_MODE'):
            logger.critical(f"Failed to send JSON response (ID: {response.get('id')}): {e}", exc_info=True)
def handle_initialize(params): # ... (wie zuvor)
    if not os.getenv('MCP_TEST_MODE'):
        logger.info(f"Handling 'initialize' request. Client Params: {params}")
    return {
        "serverInfo": {"name": "coreui-mcp-automation-server", "version": SERVER_VERSION},
        "capabilities": {"tools": TOOL_DEFINITIONS}
    }

# --- NPX Security Validation (bleibt gleich) ---
def _is_command_blocked(command_part: str) -> bool: # ... (wie zuvor)
    stripped_command_part = command_part.strip().lower()
    if not stripped_command_part: return False
    for blocked_pattern in _NPX_CONFIG["blocked_command_parts"]:
        stripped_blocked_pattern = blocked_pattern.strip().lower()
        if not stripped_blocked_pattern: continue
        if stripped_blocked_pattern in (">", "<", "|", "&", ";", "$", "..") and stripped_blocked_pattern == stripped_command_part:
             logger.warning(f"Blocked command part EXACT MATCH: '{command_part}' matches '{blocked_pattern}'")
             return True
        elif stripped_blocked_pattern not in (">", "<", "|", "&", ";", "$", ".."):
             if stripped_command_part.startswith(stripped_blocked_pattern) or \
                f" {stripped_blocked_pattern}" in stripped_command_part or \
                f"/{stripped_blocked_pattern}" in stripped_command_part :
                logger.warning(f"Blocked command part SUBSTRING/PREFIX: '{command_part}' contains '{blocked_pattern}'")
                return True
    return False
def _validate_npx_package(package_name: str, args: List[str]) -> None: # ... (wie zuvor)
    if not package_name or not package_name.strip(): raise ValueError("Package name cannot be empty.")
    if _is_command_blocked(package_name): raise ValueError(f"Package name '{package_name}' contains or matches a blocked pattern.")
    for arg_val in args:
        if _is_command_blocked(arg_val): raise ValueError(f"Argument '{arg_val}' for package '{package_name}' contains or matches a blocked pattern.")
    if _NPX_CONFIG["use_allowlist"]:
        allowed_packages_list = _NPX_CONFIG["allowed_packages"]
        if not allowed_packages_list: logger.warning("NPX allowlist is active but empty."); raise ValueError(f"Package '{package_name}' not allowed (NPX allowlist active and empty).")
        main_package_name_to_check = package_name
        if _NPX_CONFIG.get("allow_package_versions_in_name", True): main_package_name_to_check = package_name.split('@')[0]
        normalized_allowed_packages = [p.lower().strip() for p in allowed_packages_list]
        if main_package_name_to_check.lower().strip() not in normalized_allowed_packages:
            logger.warning(f"Package '{main_package_name_to_check}' (from '{package_name}') not in NPX allowlist: {allowed_packages_list}")
            raise ValueError(f"Package '{package_name}' not in allowed NPX packages.")
    logger.debug(f"NPX package '{package_name}' with args {args} passed security validation.")

# --- Tool Call Handler (bleibt strukturell gleich) ---
def handle_tool_call(params, request_id): # ... (wie zuvor)
    tool_name = params.get("name")
    arguments = params.get("arguments", {})
    if not os.getenv('MCP_TEST_MODE'): logger.info(f"Tool Call: '{tool_name}' (ID: {request_id}). Args: {list(arguments.keys())}")
    def _execute_tool():
        result_payload = {"success": False, "message": "Tool execution initiated."}
        error_payload = None
        try:
            if tool_name == "list_windows": result_payload = {"windows": tool_list_windows(arguments)}
            elif tool_name == "focus_window": title = arguments["title"]; _validate_str_arg(title, "title"); tool_focus_window(arguments); result_payload = {"success": True, "message": f"Attempted to focus window '{title}'."}
            elif tool_name == "screenshot_window": title = arguments["title"]; _validate_str_arg(title, "title"); result_payload = tool_screenshot_window(arguments)
            elif tool_name == "click_template_in_window": result_payload = tool_click_template_in_window(arguments)
            elif tool_name == "mouse_move": x, y = int(arguments["x"]), int(arguments["y"]); tool_mouse_move(arguments); result_payload = {"success": True, "message": f"Mouse moved to ({x},{y})."}
            elif tool_name == "mouse_click": x, y = int(arguments["x"]), int(arguments["y"]); button = arguments.get("button", "left"); _validate_mouse_button(button); tool_mouse_click(arguments); result_payload = {"success": True, "message": f"Mouse '{button}' click performed at ({x},{y})."}
            elif tool_name == "mouse_drag": start_x, start_y = int(arguments["start_x"]), int(arguments["start_y"]); end_x, end_y = int(arguments["end_x"]), int(arguments["end_y"]); button = arguments.get("button", "left"); _validate_mouse_button(button); tool_mouse_drag(arguments); result_payload = {"success": True, "message": f"Mouse drag from ({start_x},{start_y}) to ({end_x},{end_y}) with '{button}' button completed."}
            elif tool_name == "mouse_scroll": dx, dy = int(arguments.get("dx", 0)), int(arguments.get("dy", 0)); tool_mouse_scroll(arguments); result_payload = {"success": True, "message": f"Mouse scrolled by dx={dx}, dy={dy}."}
            elif tool_name == "keyboard_type_text": text_to_type = arguments["text"]; _validate_str_arg(text_to_type, "text", allow_empty=True); tool_keyboard_type_text(arguments); result_payload = {"success": True, "message": f"Text typed (first 30 chars): '{text_to_type[:30]}{'...' if len(text_to_type)>30 else ''}'."}
            elif tool_name == "keyboard_press_key": key_specification = arguments["key_spec"]; _validate_str_arg(key_specification, "key_spec"); tool_keyboard_press_key(arguments); result_payload = {"success": True, "message": f"Key '{key_specification}' pressed."}
            elif tool_name == "npx_execute": result_payload = tool_npx_execute(arguments)
            else: raise RuntimeError(f"Unknown tool: '{tool_name}'.")
            if "success" not in result_payload and isinstance(result_payload, dict): result_payload["success"] = True
        except (WindowNotFoundError, VisionError, ValueError, TypeError, KeyError) as e:
            if not os.getenv('MCP_TEST_MODE'): logger.warning(f"Error processing tool '{tool_name}' (ID: {request_id}): {type(e).__name__} - {e!s}")
            error_payload = {"code": -32602, "message": f"Invalid parameters or operation error: {e!s}", "data": {"tool": tool_name, "type": type(e).__name__}}
        except WindowOperationError as e:
            if not os.getenv('MCP_TEST_MODE'): logger.error(f"Window operation failure for tool '{tool_name}' (ID: {request_id}): {e!s}", exc_info=True)
            error_payload = {"code": -32000, "message": f"Window operation failed: {e!s}", "data": {"tool": tool_name, "type": type(e).__name__}}
        except subprocess.SubprocessError as e_sub:
             if not os.getenv('MCP_TEST_MODE'): logger.error(f"Subprocess execution error for tool '{tool_name}' (ID: {request_id}): {e_sub!s}", exc_info=True)
             error_payload = {"code": -32001, "message": f"Subprocess execution failed: {e_sub!s}", "data": {"tool": tool_name, "type": type(e_sub).__name__}}
        except Exception as e:
            if not os.getenv('MCP_TEST_MODE'): logger.critical(f"Unexpected server error executing tool '{tool_name}' (ID: {request_id}): {e!s}", exc_info=True)
            error_payload = {"code": -32603, "message": f"Internal server error: {type(e).__name__} - {e!s}", "data": {"tool": tool_name}}
        return result_payload, error_payload
    if executor and PARALLEL_WORKERS > 0: # ... (wie zuvor)
        future = executor.submit(_execute_tool)
        active_futures.add(future)
        def on_complete(fut):
            active_futures.discard(fut)
            try:
                result_payload, error_payload = fut.result()
                response = {"jsonrpc": "2.0", "id": request_id}
                if error_payload: response["error"] = error_payload
                else: response["result"] = result_payload
                send_response(response)
            except Exception as e:
                if not os.getenv('MCP_TEST_MODE'): logger.error(f"Error in parallel tool execution callback for tool '{tool_name}' (ID: {request_id}): {e}", exc_info=True)
                send_response({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": f"Internal error during parallel execution result processing: {e}"}})
        future.add_done_callback(on_complete)
        return None
    else: return _execute_tool()

# --- Helper Validation Functions ---
def _validate_str_arg(value: Any, arg_name: str, allow_empty: bool = False) -> None: # ... (wie zuvor)
    if not isinstance(value, str): raise TypeError(f"Argument '{arg_name}' must be a string, got {type(value).__name__}.")
    if not allow_empty and not value.strip(): raise ValueError(f"Argument '{arg_name}' must be a non-empty string.")
def _validate_mouse_button(button_val: str) -> None: # ... (wie zuvor)
    if button_val not in ["left", "right", "middle"]: raise ValueError(f"Invalid mouse button: '{button_val}'. Must be 'left', 'right', or 'middle'.")

# --- Gekürzte Tool Implementations (Logik wie zuvor) ---
def tool_list_windows(args): # ...
    if not os.getenv('MCP_TEST_MODE'): logger.debug("Executing tool_list_windows")
    async def _async_list_windows():
        all_sys_windows = await asyncio.to_thread(window.list_all_windows)
        formatted_windows = []
        for w_instance in all_sys_windows:
            try:
                title = await asyncio.to_thread(getattr, w_instance, 'title', 'Unknown')
                is_visible = await asyncio.to_thread(w_instance.is_visible) 
                if title and title != "Untitled Window" and is_visible:
                    bbox_obj: BBox = await asyncio.to_thread(getattr, w_instance, 'bbox')
                    win_id = await asyncio.to_thread(getattr, w_instance, 'window_id', 'unknown')
                    formatted_windows.append({"title": title, "window_id": win_id, "is_visible": True, "bounding_box": {"left": bbox_obj[0], "top": bbox_obj[1], "width": bbox_obj[2], "height": bbox_obj[3]}})
            except WindowOperationError as e_op: logger.debug(f"Skipping window in list due to operation error: {e_op}")
            except Exception as e_gen: logger.debug(f"Skipping window due to generic error: {e_gen}", exc_info=False)
        if not os.getenv('MCP_TEST_MODE'): logger.info(f"tool_list_windows found {len(formatted_windows)} matching windows.")
        return formatted_windows
    try: return asyncio.run(_async_list_windows())
    except Exception as e: logger.error(f"Runtime error during tool_list_windows: {e}", exc_info=True); raise RuntimeError(f"Failed to list windows: {str(e)}")
def tool_focus_window(args): # ...
    title = args["title"]; _validate_str_arg(title, "title")
    if not os.getenv('MCP_TEST_MODE'): logger.debug(f"Executing tool_focus_window for title: '{title}'")
    async def _async_focus_window():
        target_win = await asyncio.to_thread(window.get_window, title=title)
        await asyncio.to_thread(target_win.activate)
        if not os.getenv('MCP_TEST_MODE'): logger.info(f"Window '{await asyncio.to_thread(getattr, target_win, 'title', title)}' focused attempt.")
    try: asyncio.run(_async_focus_window())
    except WindowNotFoundError: raise
    except Exception as e: logger.error(f"Runtime error: {e}", exc_info=True); raise RuntimeError(f"Failed to focus window '{title}': {str(e)}")
def tool_screenshot_window(args): # ...
    title = args["title"]; _validate_str_arg(title, "title")
    if not os.getenv('MCP_TEST_MODE'): logger.debug(f"Executing tool_screenshot_window for title: '{title}'")
    async def _async_screenshot_window():
        target_win = await asyncio.to_thread(window.get_window, title=title)
        win_bbox_actual: BBox = await asyncio.to_thread(getattr, target_win, 'bbox')
        img = await capture.screenshot_async(win_bbox_actual, img_format="PNG")
        buffer = io.BytesIO(); await asyncio.to_thread(img.save, buffer, format="PNG", optimize=True)
        img_b64_str = base64.b64encode(buffer.getvalue()).decode('utf-8')
        if not os.getenv('MCP_TEST_MODE'): logger.info(f"Screenshot for '{await asyncio.to_thread(getattr, target_win, 'title', title)}'. Size: {img.width}x{img.height}")
        return {"image_base64": img_b64_str, "width": img.width, "height": img.height, "format": "PNG"}
    try: return asyncio.run(_async_screenshot_window())
    except WindowNotFoundError: raise
    except Exception as e: logger.error(f"Runtime error: {e}", exc_info=True); raise RuntimeError(f"Failed screenshot for '{title}': {str(e)}")
def tool_click_template_in_window(args): # ...
    window_title = args["window_title"]; _validate_str_arg(window_title, "window_title")
    template_b64 = args["template_base64"]; _validate_str_arg(template_b64, "template_base64")
    threshold = float(args.get("threshold", 0.8))
    if not os.getenv('MCP_TEST_MODE'): logger.debug(f"Executing tool_click_template_in_window: '{window_title}', thr: {threshold:.2f}")
    async def _async_click_template():
        try: template_img = await asyncio.to_thread(Image.open, io.BytesIO(base64.b64decode(template_b64)))
        except Exception as e_img: raise ValueError(f"Invalid base64 template: {e_img!s}") from e_img
        target_win = await asyncio.to_thread(window.get_window, title=window_title)
        win_bbox_actual: BBox = await asyncio.to_thread(getattr, target_win, 'bbox')
        screenshot_img = await capture.screenshot_async(win_bbox_actual)
        try:
            detector = await asyncio.to_thread(vision.TemplateMatcher, template_img, threshold=threshold)
            detection_result: Optional[Detection] = await asyncio.to_thread(vision.locate, screenshot_img, detector)
        except Exception as e_vis: logger.error(f"Vision error: {e_vis}", exc_info=True); raise VisionError(f"Template matching failed: {str(e_vis)}") from e_vis
        if detection_result:
            click_pos_abs = (win_bbox_actual[0] + detection_result.center[0], win_bbox_actual[1] + detection_result.center[1])
            await asyncio.to_thread(input_backend.click, click_pos_abs)
            actual_title = await asyncio.to_thread(getattr, target_win, 'title', window_title)
            msg = f"Template clicked in '{actual_title}' at {click_pos_abs} (Conf: {detection_result.score:.3f})."
            if not os.getenv('MCP_TEST_MODE'): logger.info(msg)
            return {"success": True, "message": msg, "clicked_at": click_pos_abs, "confidence": round(detection_result.score, 3)}
        else:
            actual_title = await asyncio.to_thread(getattr, target_win, 'title', window_title)
            msg = f"Template not found in '{actual_title}' (thr: {threshold:.2f})."
            if not os.getenv('MCP_TEST_MODE'): logger.warning(msg)
            return {"success": False, "message": msg, "match_found": False}
    try: return asyncio.run(_async_click_template())
    except (WindowNotFoundError, ValueError, VisionError): raise
    except Exception as e: logger.error(f"Runtime error: {e}", exc_info=True); raise RuntimeError(f"Failed click template in '{window_title}': {str(e)}")
def tool_mouse_move(args): # ...
    x, y = int(args["x"]), int(args["y"])
    if not os.getenv('MCP_TEST_MODE'): logger.debug(f"tool_mouse_move to ({x}, {y})")
    async def _async_op(): await asyncio.to_thread(input_backend.move, (x,y)); logger.info(f"Mouse moved to ({x},{y}).")
    try: asyncio.run(_async_op())
    except Exception as e: raise RuntimeError(f"Failed mouse move: {e!s}")
def tool_mouse_click(args): # ...
    x,y,button = int(args["x"]),int(args["y"]),args.get("button","left"); _validate_mouse_button(button)
    if not os.getenv('MCP_TEST_MODE'): logger.debug(f"tool_mouse_click: {button} at ({x},{y})")
    async def _async_op(): await asyncio.to_thread(input_backend.click,(x,y),button); logger.info(f"Mouse {button} click at ({x},{y}).")
    try: asyncio.run(_async_op())
    except Exception as e: raise RuntimeError(f"Failed mouse click: {e!s}")
def tool_mouse_drag(args): # ...
    sx,sy,ex,ey,button,dur = int(args["start_x"]),int(args["start_y"]),int(args["end_x"]),int(args["end_y"]),args.get("button","left"),float(args.get("duration_s",0.5)); _validate_mouse_button(button)
    if not os.getenv('MCP_TEST_MODE'): logger.debug(f"tool_mouse_drag from ({sx},{sy}) to ({ex},{ey}), btn:{button}, dur:{dur}s")
    async def _async_op(): await asyncio.to_thread(input_backend.drag,(sx,sy),(ex,ey),button,dur); logger.info(f"Mouse drag from ({sx},{sy}) to ({ex},{ey}) with {button} completed.")
    try: asyncio.run(_async_op())
    except Exception as e: raise RuntimeError(f"Failed mouse drag: {e!s}")
def tool_mouse_scroll(args): # ...
    dx,dy = int(args.get("dx",0)),int(args.get("dy",0))
    if not os.getenv('MCP_TEST_MODE'): logger.debug(f"tool_mouse_scroll: dx={dx}, dy={dy}")
    async def _async_op(): await asyncio.to_thread(input_backend.scroll,dx,dy); logger.info(f"Mouse scrolled dx={dx}, dy={dy}.")
    try: asyncio.run(_async_op())
    except Exception as e: raise RuntimeError(f"Failed mouse scroll: {e!s}")
def tool_keyboard_type_text(args): # ...
    text = args["text"]; _validate_str_arg(text, "text", allow_empty=True)
    if not os.getenv('MCP_TEST_MODE'): logger.debug(f"tool_keyboard_type_text: '{text[:50]}...'")
    async def _async_op(): await asyncio.to_thread(input_backend.type_text,text); logger.info(f"Text typed: '{text[:50]}...'.")
    try: asyncio.run(_async_op())
    except Exception as e: raise RuntimeError(f"Failed to type text: {e!s}")
def tool_keyboard_press_key(args): # ...
    key_spec = args["key_spec"]; _validate_str_arg(key_spec, "key_spec")
    if not os.getenv('MCP_TEST_MODE'): logger.debug(f"tool_keyboard_press_key: '{key_spec}'")
    async def _async_op():
        success = False
        try: await asyncio.to_thread(input_backend.press, key_spec); success = True
        except (TypeError, AttributeError, NotImplementedError) as e_p:
            logger.debug(f"input_backend.press('{key_spec}') failed: {e_p}. Trying key_press.")
            if hasattr(input_backend, 'key_press'): await asyncio.to_thread(input_backend.key_press, key_spec); success = True # type: ignore
            else: logger.warning(f"Neither 'press' nor 'key_press' for '{key_spec}'.")
        if not success and not os.getenv('MCP_TEST_MODE'): raise RuntimeError(f"Key press method for '{key_spec}' failed.")
        if success and not os.getenv('MCP_TEST_MODE'): logger.info(f"Key '{key_spec}' pressed.")
    try: asyncio.run(_async_op())
    except RuntimeError: raise
    except Exception as e:
        if not os.getenv('MCP_TEST_MODE'): logger.error(f"Error pressing key '{key_spec}': {e}", exc_info=True); raise RuntimeError(f"Failed to press key '{key_spec}': {e!s}")

# --- Überarbeitetes tool_npx_execute ---
def tool_npx_execute(args: Dict[str, Any]) -> Dict[str, Any]:
    package_name = args.get("package")
    cmd_args: List[str] = args.get("args", []) # Bleibt als Standard []
    working_dir_str: str = args.get("workingDirectory", ".")
    # NEU: Umgebungsvariablen aus dem Tool-Aufruf
    call_specific_env: Dict[str, str] = args.get("env", {}) 

    _validate_str_arg(package_name, "package")
    if not isinstance(cmd_args, list) or not all(isinstance(item, str) for item in cmd_args):
        raise ValueError("'args' must be a list of strings.")
    if not isinstance(working_dir_str, str):
        raise ValueError("'workingDirectory' must be a string.")
    if not isinstance(call_specific_env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in call_specific_env.items()):
        raise ValueError("'env' must be a dictionary of string key-value pairs.")

    _validate_npx_package(package_name, cmd_args)

    npx_command_list = ['npx', package_name] + cmd_args
    command_str_for_log = ' '.join(npx_command_list)
    
    resolved_working_dir = Path(working_dir_str).resolve()
    if not resolved_working_dir.is_dir():
        logger.error(f"NPX Working Directory '{resolved_working_dir}' not found or not a directory.")
        raise FileNotFoundError(f"NPX working directory not found: {resolved_working_dir}")

    # Umgebungsvariablen zusammenführen: System -> Default Config -> Call Specific
    current_env = os.environ.copy() # Start mit System-Umgebung
    current_env.update(_NPX_CONFIG.get("default_env_vars", {})) # Überschreiben/Ergänzen mit Defaults aus config.json
    current_env.update(call_specific_env) # Überschreiben/Ergänzen mit aufrufspezifischen Variablen

    # Timeout aus der Konfiguration holen
    execution_timeout = _NPX_CONFIG.get("execution_timeout_seconds", 300) # Fallback, falls nicht in _NPX_CONFIG

    if not os.getenv('MCP_TEST_MODE'):
        logger.info(f"Executing NPX: '{command_str_for_log}' in WD: '{resolved_working_dir}' with Timeout: {execution_timeout}s")
        # Logge nur Keys der zusätzlichen Env-Vars, nicht die Werte (könnten sensitiv sein)
        sensitive_env_keys = list(_NPX_CONFIG.get("default_env_vars", {}).keys()) + list(call_specific_env.keys())
        if sensitive_env_keys:
            logger.debug(f"NPX custom environment keys: {list(set(sensitive_env_keys))}")


    try:
        npx_executable_path = shutil.which("npx")
        if not npx_executable_path:
            logger.error("'npx' command not found in system PATH. Please ensure Node.js/npm is installed correctly.")
            raise FileNotFoundError("'npx' executable not found in PATH.")
        
        # Ersetze 'npx' in der Befehlsliste mit dem vollen Pfad für mehr Robustheit
        npx_command_list[0] = npx_executable_path

        process_result = subprocess.run(
            npx_command_list,
            cwd=str(resolved_working_dir),
            capture_output=True,
            text=True, # Standardmäßig UTF-8 auf vielen Systemen, ansonsten locale.getpreferredencoding()
            timeout=execution_timeout,
            env=current_env, # Umgebungsvariablen übergeben
            check=False # Fehler selbst behandeln
        )
        stdout_data = process_result.stdout.strip()
        stderr_data = process_result.stderr.strip()
        exit_code = process_result.returncode

        if not os.getenv('MCP_TEST_MODE'):
            logger.info(f"NPX '{command_str_for_log}' finished with exit code {exit_code}.")
            # Gekürztes Logging für stdout/stderr
            if stdout_data: logger.debug(f"NPX stdout (first 1KB):\n{stdout_data[:1024]}{'...' if len(stdout_data)>1024 else ''}")
            if stderr_data: logger.debug(f"NPX stderr (first 1KB):\n{stderr_data[:1024]}{'...' if len(stderr_data)>1024 else ''}")
        
        output_data = {
            "commandExecuted": command_str_for_log,
            "exitCode": exit_code,
            "stdout": stdout_data,
            "stderr": stderr_data,
            "success": exit_code == 0
        }
        result_text_json = json.dumps(output_data, indent=2)
        return {
            "success": exit_code == 0,
            "message": f"NPX command executed. Exit code: {exit_code}.",
            "content": [{"type": "text", "text": result_text_json}]
        }
    except subprocess.TimeoutExpired:
        logger.error(f"NPX command '{command_str_for_log}' timed out after {execution_timeout} seconds.")
        raise subprocess.SubprocessError(f"NPX command '{package_name}' timed out after {execution_timeout}s.")
    except FileNotFoundError as e_fnf: # z.B. npx nicht gefunden, obwohl shutil.which es finden sollte (selten)
        logger.error(f"FileNotFoundError during NPX execution for '{command_str_for_log}': {e_fnf}")
        raise
    except Exception as e_run: # Andere Fehler von subprocess.run
        logger.error(f"Unexpected error executing NPX command '{command_str_for_log}': {e_run}", exc_info=True)
        raise subprocess.SubprocessError(f"Failed to execute NPX command '{package_name}': {e_run!s}")

def main():
    try:
        _load_npx_config() # NPX Konfiguration laden
        init_parallel_processing()
        if not os.getenv('MCP_TEST_MODE'):
            logger.info(f"CoreUI-MCP Server v{SERVER_VERSION} (Python {sys.version_info.major}.{sys.version_info.minor}) starting. Listening on stdin...")
        # ... (Rest der main-Funktion wie zuvor) ...
        while True:
            line = sys.stdin.readline()
            if not line:
                if not os.getenv('MCP_TEST_MODE'): logger.info("Stdin closed. Server shutting down.")
                break
            line = line.strip()
            if not line: continue
            try:
                request = json.loads(line)
                if not isinstance(request, dict): raise json.JSONDecodeError("Input not JSON object.", line, 0)
            except json.JSONDecodeError as e_json:
                if not os.getenv('MCP_TEST_MODE'): logger.error(f"Invalid JSON: '{line}'. Error: {e_json!s}")
                send_response({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {e_json.msg}"}})
                continue
            
            method, params_data, request_id = request.get("method"), request.get("params",{}), request.get("id")
            if not os.getenv('MCP_TEST_MODE'): logger.info(f"Request: Method='{method}', ID='{request_id}', ParamKeys={list(params_data.keys()) if isinstance(params_data,dict) else 'N/A'}")

            if request_id is None and method != "shutdown": 
                if method == "notifications/initialized": logger.info("Client 'initialized' notification.")
                else: logger.debug(f"Unhandled notification: '{method}'.")
                continue
            try:
                if method == "initialize": result = handle_initialize(params_data)
                elif method == "tools/call":
                    tool_name = params_data.get("name")
                    if not tool_name: raise ValueError("Tool 'name' missing in tools/call params.")
                    result_tuple = handle_tool_call(params_data, request_id) 
                    if result_tuple is not None:
                        result_payload, error_payload = result_tuple
                        response = {"jsonrpc": "2.0", "id": request_id}
                        if error_payload: response["error"] = error_payload
                        else: response["result"] = result_payload
                        send_response(response)
                    continue 
                elif method == "shutdown":
                    if not os.getenv('MCP_TEST_MODE'): logger.info(f"Shutdown request (ID: {request_id}).")
                    send_response({"jsonrpc": "2.0", "id": request_id, "result": "Server shutting down."}); break
                else: raise NotImplementedError(f"Method '{method}' not found.")
                
                response = {"jsonrpc": "2.0", "id": request_id, "result": result}
                send_response(response)

            except NotImplementedError as e_ni:
                if not os.getenv('MCP_TEST_MODE'): logger.warning(f"Method not found: '{method}' (ID: {request_id})")
                send_response({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": str(e_ni)}})
            except ValueError as e_val: 
                if not os.getenv('MCP_TEST_MODE'): logger.warning(f"Invalid params for '{method}' (ID: {request_id}): {e_val}")
                send_response({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": f"Invalid parameters: {e_val}"}})
            except Exception as e_proc:
                if not os.getenv('MCP_TEST_MODE'): logger.error(f"Error processing '{method}' (ID: {request_id}): {e_proc}", exc_info=True)
                send_response({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": f"Internal error: {str(e_proc)}"}});
    except KeyboardInterrupt:
        if not os.getenv('MCP_TEST_MODE'): logger.info("Server shutdown by KeyboardInterrupt.")
    except Exception as e_top:
        if not os.getenv('MCP_TEST_MODE'): logger.critical(f"Top-level unrecoverable error: {e_top!s}", exc_info=True)
        sys.stderr.write(f"CRITICAL SERVER ERROR: {e_top}\n"); sys.exit(1)
    finally:
        cleanup_parallel_processing()
        if not os.getenv('MCP_TEST_MODE'): logger.info("CoreUI-MCP Server process shut down.")

if __name__ == "__main__":
    main()
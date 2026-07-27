"""Project-wide constants."""

# ---------------------------------------------------------------------------
# ANSI escape codes — terminal output formatting
# ---------------------------------------------------------------------------

# Text styles
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

# Foreground colours
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
MAGENTA = "\033[95m"
WHITE = "\033[97m"

# Background colours
BG_BLUE = "\033[44m"
BG_CYAN = "\033[46m"

# Cursor / line control
CLEAR_LINE = "\033[2K\r"
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"
SAVE_POS = "\033[s"
RESTORE_POS = "\033[u"

# ---------------------------------------------------------------------------
# Sanity check node
# ---------------------------------------------------------------------------

# Max retries per phase before escalating instead of looping
MAX_PHASE_RETRIES = 10

# File extension groups used when scanning changed files
JS_TS_EXTENSIONS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
GO_EXTENSIONS = (".go",)

# ---------------------------------------------------------------------------
# Git manager node
# ---------------------------------------------------------------------------

# Paths excluded from `git add` staging so agent metadata is never committed
IGNORED_STAGE_PATHS = (
    ".ai-sdlc",
    ".ai-sdlc/**",
    "runs",
    "runs/**",
)

# ---------------------------------------------------------------------------
# Tool agent node
# ---------------------------------------------------------------------------

# Hard limits for write_file tool calls to keep token usage manageable
MAX_WRITE_FILE_CHARS = 5000
MAX_WRITE_FILE_LINES = 120

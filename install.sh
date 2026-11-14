#!/usr/bin/env bash
# Installs Shell Hunter: private venv with psutil + a `shell-hunter` command on your PATH.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="${SHELL_HUNTER_HOME:-$HOME/.shell-hunter}"
BIN_DIR="${SHELL_HUNTER_BIN:-$HOME/.local/bin}"

PY="$(command -v python3 || command -v python || true)"
[ -n "$PY" ] || { echo "python3 not found. Install it first (macOS: brew install python)." >&2; exit 1; }
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 8))' || { echo "Python 3.8+ required." >&2; exit 1; }

echo "==> Installing to $HOME_DIR"
mkdir -p "$HOME_DIR" "$BIN_DIR"
cp "$SRC/shell_hunter.py" "$HOME_DIR/shell_hunter.py"

# a venv avoids macOS/Homebrew "externally-managed-environment" pip errors
"$PY" -m venv "$HOME_DIR/venv"
VPY="$HOME_DIR/venv/bin/python"
[ -x "$VPY" ] || VPY="$HOME_DIR/venv/Scripts/python.exe"   # Git Bash on Windows
"$VPY" -m pip install --quiet --upgrade pip psutil

cat > "$BIN_DIR/shell-hunter" <<WRAP
#!/usr/bin/env bash
exec "$VPY" "$HOME_DIR/shell_hunter.py" "\$@"
WRAP
chmod +x "$BIN_DIR/shell-hunter"

case ":$PATH:" in
  *":$BIN_DIR:"*) echo "==> $BIN_DIR is already on PATH" ;;
  *)
    case "$(basename "${SHELL:-}")" in
      zsh)  RC="$HOME/.zshrc" ;;
      bash) if [ "$(uname)" = Darwin ] || [ -f "$HOME/.bash_profile" ]; then RC="$HOME/.bash_profile"; else RC="$HOME/.bashrc"; fi ;;
      *)    RC="$HOME/.profile" ;;
    esac
    touch "$RC"
    if ! grep -qF "$BIN_DIR" "$RC"; then
      printf '\n# Shell Hunter\nexport PATH="%s:$PATH"\n' "$BIN_DIR" >> "$RC"
    fi
    echo "==> Added $BIN_DIR to PATH in $RC (open a new terminal, or: source $RC)"
    ;;
esac

echo "Done. Run: shell-hunter   (or: shell-hunter --once)"

#!/bin/zsh

set -u

SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR" || exit 1

PYTHON_BIN=""
for candidate in \
  "$SCRIPT_DIR/.venv/bin/python" \
  "$SCRIPT_DIR/venv/bin/python" \
  "$(command -v python3 2>/dev/null)" \
  "$(command -v python 2>/dev/null)"
do
  if [[ -n "$candidate" && -x "$candidate" ]]; then
    PYTHON_BIN="$candidate"
    break
  fi
done

if [[ -z "$PYTHON_BIN" ]]; then
  echo "Could not find a Python interpreter."
  echo "Create the project's virtualenv or install python3, then try again."
  echo
  read -r "?Press return to close this window..."
  exit 1
fi

ARGS=("$@")
if [[ ${#ARGS[@]} -eq 0 ]]; then
  ARGS=(--app)
fi

echo "Launching Insta Autolayout from:"
echo "  $SCRIPT_DIR"
echo "Using Python:"
echo "  $PYTHON_BIN"
echo

"$PYTHON_BIN" -m insta_autolayout "${ARGS[@]}"
STATUS=$?

if [[ $STATUS -ne 0 ]]; then
  echo
  echo "Insta Autolayout exited with status $STATUS."
  read -r "?Press return to close this window..."
fi

exit $STATUS

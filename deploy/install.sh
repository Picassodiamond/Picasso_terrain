#!/bin/bash
# Server-side install for cPanel (Apache + Phusion Passenger).
#
# Run it from the SSH terminal AFTER "Setup Python App" has created the application, and after you
# have entered the virtualenv with the "source ..." command that cPanel shows on the app screen:
#
#     source /home/USER/virtualenv/plm/3.12/bin/activate && cd /home/USER/plm
#     bash deploy/install.sh
#
# It installs the dependencies, creates the data directory outside public_html, runs the test suite
# once to prove the machine can do the geometry, and prints the cron line for the job worker.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${PLM_DATA_DIR:-$HOME/plm_data}"

echo "== application : $APP_DIR"
echo "== data        : $DATA_DIR"
echo "== python      : $(python -V 2>&1)  ($(command -v python))"

case "$(command -v python)" in
  *virtualenv*|*venv*) ;;
  *) echo "!! this is not the application's virtualenv - run the 'source .../activate' line cPanel shows, then try again" >&2
     exit 1 ;;
esac

echo
echo "== installing dependencies (wheels only; no compiler needed)"
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e "$APP_DIR[server]"

echo
echo "== data directory"
mkdir -p "$DATA_DIR/projects" "$DATA_DIR/tmp" "$DATA_DIR/archive"
chmod 700 "$DATA_DIR"
echo "   $DATA_DIR ready (mode $(stat -c '%a' "$DATA_DIR"))"

echo
echo "== proving the geometry engine on this machine"
# One file at a time: the whole suite in a single interpreter segfaults on shared hosting (numpy,
# scipy, triangle and many short-lived event loops in one process), while every file passes alone.
failed=0
for f in "$APP_DIR"/tests/test_*.py; do
  if out=$(python -m pytest -q --no-header "$f" 2>&1); then
    printf '   %-28s %s\n' "$(basename "$f")" "$(echo "$out" | grep -Eo '[0-9]+ passed[^,]*' | head -1)"
  else
    failed=$((failed + 1))
    printf '!! %-28s FAILED\n' "$(basename "$f")" >&2
    echo "$out" | tail -15 >&2
  fi
done
if [ "$failed" -eq 0 ]; then
  echo "   all test files passed"
else
  echo "!! $failed test file(s) failed - do not go live until this is understood" >&2
fi

echo
echo "== import check through the Passenger entry point"
cd "$APP_DIR" && python -c "
import passenger_wsgi
print('   WSGI application:', type(passenger_wsgi.application).__name__)
"

echo
echo "== next steps"
echo "1. cPanel -> Setup Python App -> Restart"
echo "2. https://YOUR-DOMAIN/api/health   should answer {\"status\":\"ok\", ...}"
echo "3. Add this cron job (cPanel -> Cron Jobs, every minute) so queued jobs finish:"
echo
echo "   cd $APP_DIR && $(command -v python) -m plm.worker >> $DATA_DIR/worker.log 2>&1"
echo
echo "4. Register yourself at https://YOUR-DOMAIN/ (the first account becomes administrator),"
echo "   then set PLM_OPEN_REGISTRATION=0 in the Python App screen and restart."

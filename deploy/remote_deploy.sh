#!/bin/bash
# Upload and install Picasso LandMesh on a cPanel account over SSH, then prove it from outside.
#
#     bash deploy/remote_deploy.sh --host server.example.com --user cpaneluser \
#                                  --domain https://plm.example.com [--port 22] [--key ~/.ssh/plm_cpanel]
#
# Run it from the repository root, after `cd web && npm run build`. Safe to run again for an
# upgrade: it replaces the application files and never touches the data directory.
#
# The cPanel "Setup Python App" entry must already exist (that part cannot be done over SSH). If its
# virtualenv is missing the script says so and stops before changing anything on the server.
set -euo pipefail

HOST=""; USER_NAME=""; DOMAIN=""; PORT=22; KEY="$HOME/.ssh/plm_cpanel"; APP_NAME="plm"
while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --user) USER_NAME="$2"; shift 2 ;;
    --domain) DOMAIN="${2%/}"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --key) KEY="$2"; shift 2 ;;
    --app) APP_NAME="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
[ -n "$HOST" ] && [ -n "$USER_NAME" ] && [ -n "$DOMAIN" ] || { sed -n '2,10p' "$0"; exit 2; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH=(ssh -i "$KEY" -p "$PORT" -o StrictHostKeyChecking=accept-new -o BatchMode=yes "$USER_NAME@$HOST")
REMOTE_HOME="/home/$USER_NAME"
APP_DIR="$REMOTE_HOME/$APP_NAME"
DATA_DIR="$REMOTE_HOME/${APP_NAME}_data"

say() { printf '\n== %s\n' "$1"; }

say "1/7  building the upload bundle"
ZIP="$(python "$ROOT/deploy/make_bundle.py" | grep -o '[^ ]*plm-deploy-[^ ]*\.zip' | head -1)"
[ -f "$ZIP" ] || { echo "the bundle was not produced" >&2; exit 1; }
ls -lh "$ZIP" | awk '{print "   " $NF, $5}'

say "2/7  checking the connection and the application virtualenv"
"${SSH[@]}" "echo '   connected as' \$(whoami) 'on' \$(hostname)"
VENV_PY="$("${SSH[@]}" "ls -d $REMOTE_HOME/virtualenv/$APP_NAME/*/bin/python 2>/dev/null | head -1" || true)"
if [ -z "$VENV_PY" ]; then
  echo "!! no virtualenv at $REMOTE_HOME/virtualenv/$APP_NAME/<version>/" >&2
  echo "   Create the app first: cPanel -> Setup Python App -> Create," >&2
  echo "   application root '$APP_NAME', startup file 'passenger_wsgi.py', entry point 'application'." >&2
  exit 1
fi
echo "   interpreter: $VENV_PY"

say "3/7  uploading"
scp -i "$KEY" -P "$PORT" -o StrictHostKeyChecking=accept-new "$ZIP" "$USER_NAME@$HOST:$REMOTE_HOME/"

say "4/7  unpacking into $APP_DIR (data directory untouched)"
"${SSH[@]}" "cd $REMOTE_HOME && unzip -oq $(basename "$ZIP") && rm -f $(basename "$ZIP") && echo '   unpacked' && ls $APP_DIR | tr '\n' ' '"

say "5/7  installing"
"${SSH[@]}" "cd $APP_DIR && PLM_DATA_DIR=$DATA_DIR PATH=$(dirname "$VENV_PY"):\$PATH bash deploy/install.sh" || {
  echo "!! the install script failed - read the output above" >&2; exit 1; }

say "6/7  restarting the Passenger application"
"${SSH[@]}" "mkdir -p $APP_DIR/tmp && touch $APP_DIR/tmp/restart.txt && echo '   restart requested'"
sleep 6

say "7/7  verifying $DOMAIN from outside"
python "$ROOT/deploy/check_live.py" "$DOMAIN"

cat <<EOF

Done. Remaining manual steps, if this was the first deployment:
  * cPanel -> Cron Jobs, every minute:
      cd $APP_DIR && $VENV_PY -m plm.worker >> $DATA_DIR/worker.log 2>&1
  * open $DOMAIN/ , fill the visitor form and register the first (administrator) account
  * then set PLM_OPEN_REGISTRATION=0 in the Python App screen and restart
EOF

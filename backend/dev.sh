# Set up aws cred first
if [ -f .env.local ]; then
  set -a; . .env.local; set +a
fi
source .venv/bin/activate && python dev_server.py

#!/bin/sh
# Run as root after copying publish.py, portal.py and the prepared public key.
set -eu
TASK_ROOT=/srv/latex-review
TASK_CODE=/opt/latex-review
if ! id latex-review >/dev/null 2>&1; then
    useradd --create-home --shell /bin/sh latex-review
fi
install -d -m 755 "$TASK_CODE" "$TASK_ROOT" "$TASK_ROOT/site" /var/www/latex-review-acme
install -m 644 /tmp/latex-review-setup/publish.py "$TASK_CODE/publish.py"
install -m 644 /tmp/latex-review-setup/portal.py "$TASK_CODE/portal.py"
if [ ! -f "$TASK_ROOT/papers.json" ]; then
    install -m 644 /tmp/latex-review-setup/papers.json "$TASK_ROOT/papers.json"
fi
chown -R latex-review:latex-review "$TASK_ROOT"
install -d -m 700 -o latex-review -g latex-review /home/latex-review/.ssh
python3 - <<'PY'
from pathlib import Path
key = Path('/tmp/latex-review-setup/upload.pub').read_text().strip()
line = 'restrict,command="/usr/bin/python3 /opt/latex-review/publish.py /srv/latex-review" ' + key
path = Path('/home/latex-review/.ssh/authorized_keys')
existing = path.read_text().splitlines() if path.exists() else []
if line not in existing:
    path.write_text('\n'.join([*existing, line]) + '\n')
PY
chmod 600 /home/latex-review/.ssh/authorized_keys
chown latex-review:latex-review /home/latex-review/.ssh/authorized_keys
install -d -m 750 -o root -g www /etc/latex-review
if [ ! -f /etc/latex-review/htpasswd ]; then
    install -m 640 -o root -g www /tmp/latex-review-setup/htpasswd /etc/latex-review/htpasswd
fi
su -s /bin/sh latex-review -c 'python3 /opt/latex-review/publish.py /srv/latex-review --init'
install -m 644 /tmp/latex-review-setup/reports-http.conf /www/server/panel/vhost/nginx/reports.gpare.top.conf
/www/server/nginx/sbin/nginx -t
/www/server/nginx/sbin/nginx -s reload

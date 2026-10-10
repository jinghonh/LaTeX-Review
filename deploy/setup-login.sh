#!/bin/sh
# Files must be staged in /tmp/latex-review-login. Preserve existing credentials.
set -eu
TASK_STAGE=/tmp/latex-review-login
TASK_CODE=/opt/latex-review
TASK_AUTH=/etc/latex-review
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq gunicorn
if ! id latex-review-web >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin latex-review-web
fi
install -d -m 755 "$TASK_AUTH"
install -m 644 "$TASK_STAGE/web.py" "$TASK_STAGE/login.html" "$TASK_STAGE/portal.py" "$TASK_CODE/"
if [ ! -f "$TASK_AUTH/login.json" ]; then
    install -m 640 -o root -g latex-review-web "$TASK_STAGE/login.json" "$TASK_AUTH/login.json"
fi
if [ ! -f "$TASK_AUTH/session.key" ]; then
    install -m 640 -o root -g latex-review-web "$TASK_STAGE/session.key" "$TASK_AUTH/session.key"
fi
install -m 644 "$TASK_STAGE/latex-review-web.service" /etc/systemd/system/latex-review-web.service
systemctl daemon-reload
systemctl enable --now latex-review-web.service
systemctl restart latex-review-web.service
python3 - <<'PY'
import time
from urllib.request import urlopen
for attempt in range(20):
    try:
        with urlopen('http://127.0.0.1:8768/login', timeout=2) as response:
            assert response.status == 200
        break
    except Exception:
        if attempt == 19:
            raise
        time.sleep(.25)
print('Login service health: passed')
PY
TASK_NGINX=/www/server/panel/vhost/nginx/reports.gpare.top.conf
if [ ! -f "$TASK_AUTH/nginx-before-login.conf" ]; then
    install -m 600 "$TASK_NGINX" "$TASK_AUTH/nginx-before-login.conf"
fi
cp "$TASK_NGINX" "$TASK_STAGE/nginx-backup.conf"
install -m 644 "$TASK_STAGE/reports-https.conf" "$TASK_NGINX"
if ! /www/server/nginx/sbin/nginx -t; then
    install -m 644 "$TASK_STAGE/nginx-backup.conf" "$TASK_NGINX"
    exit 1
fi
/www/server/nginx/sbin/nginx -s reload
su -s /bin/sh latex-review -c 'python3 /opt/latex-review/publish.py /srv/latex-review --init'

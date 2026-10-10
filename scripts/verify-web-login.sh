#!/bin/sh
# Exercise a real browser submission without using the site's actual password.
set -eu
TASK_CLI=${PWCLI:-$HOME/.codex/skills/playwright/scripts/playwright_cli.sh}
TASK_URL=${1:-https://reports.gpare.top/login}
trap '"$TASK_CLI" --session review-login-regression close >/dev/null 2>&1 || true' EXIT
"$TASK_CLI" --session review-login-regression open "$TASK_URL"
"$TASK_CLI" --session review-login-regression run-code 'async (page) => {
  await page.getByRole("textbox", {name:"账号", exact:true}).fill("reviewer");
  await page.getByRole("textbox", {name:"密码", exact:true}).fill("wrong-password-for-regression");
  const [response] = await Promise.all([
    page.waitForResponse(r=>r.request().method()==="POST" && new URL(r.url()).pathname==="/login"),
    page.getByRole("button", {name:"进入审阅空间"}).click()
  ]);
  const headers = await response.request().allHeaders();
  const message = await page.locator(".error").textContent();
  if (response.status() !== 401 || !message.includes("账号或密码不正确"))
    throw new Error("登录提交回归：" + response.status() + " / " + message);
  if (headers.origin !== new URL(page.url()).origin)
    throw new Error("登录请求未携带正确来源");
  return {status:response.status(), origin:headers.origin, message};
}'

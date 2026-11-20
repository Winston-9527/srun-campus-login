# 深圳大学校园网登录脚本

在 Linux 服务器上通过命令行登录深圳大学的深澜（SRun）校园网，也可以查询认证状态或退出当前会话。默认使用深大认证网关 `https://net.szu.edu.cn`，认证入口编号为 `12`。

## 迁移到另一台服务器

只需复制 `srun_login.py`。脚本使用 Python 3 标准库，无需 `pip install`，也没有配套的 Python 模块。请确认目标服务器能直连校园网认证网关。脚本会直连网关，环境变量里的 HTTP 代理设置不会生效。

复制脚本后可直接运行。

```bash
python3 srun_login.py --status
python3 srun_login.py
```

如果目标服务器所在网段使用其他网关或认证入口，可以用 `SRUN_URL` 和 `SRUN_AC_ID` 覆盖默认值。命令行参数 `--url` 和 `--ac-id` 优先于环境变量。

## 账号配置

默认交互式输入账号和密码。无人值守时可以注入 `SRUN_USER` 和 `SRUN_PASS`，或在目标服务器当前用户的家目录里配置 `~/.srun_cred`。文件第一行是账号，第二行是密码。两项环境变量均非空时优先使用它们，其次读取凭据文件。

```bash
chmod 600 ~/.srun_cred
```

在每台服务器上单独配置凭据，避免将凭据文件提交到 Git，也避免把明文密码直接写进交互式命令历史。

## 服务器自动重连

`systemd/srun-reconnect.timer` 在启动后约 15 秒运行首次检查，之后每隔约 30 分钟运行一次 `srun_login.py --reconnect`。脚本先查询校园网认证状态，收到 `not_online_error` 才读取凭据并尝试登录一次。网关无法访问时，会在下次定时检查时再试。服务器需要在未认证时仍能解析并直连校园网网关。

推荐使用 systemd 250 或更新版本。服务以独立的非特权身份运行，systemd 启动服务时解密凭据，Python 从服务专用的只读文件读取。账号和密码通过终端输入，磁盘上保存的是加密文件。自动重连会校验 HTTPS 证书并拒绝重定向。

在目标服务器的仓库目录安装脚本和定时器。

```bash
sudo install -d -m 0755 /opt/srun-login
sudo install -m 0644 srun_login.py /opt/srun-login/srun_login.py
sudo install -m 0644 systemd/srun-reconnect.{service,timer} /etc/systemd/system/
```

在服务器终端执行下面的命令，按提示输入校园网账号和密码。更换密码后，重新执行同一段命令即可更新凭据。

```bash
sudo bash -c '
set -euo pipefail
umask 077
install -d -m 0700 /etc/srun-login
read -r -p "校园网账号: " srun_user
IFS= read -r -s -p "校园网密码: " srun_pass
printf "\n" >&2
if [[ ! $srun_user =~ [^[:space:]] || -z $srun_pass ]]; then
    printf "账号和密码不能为空\n" >&2
    exit 1
fi
tmp=$(mktemp /etc/srun-login/.srun.cred.XXXXXX)
trap "rm -f -- \"\$tmp\"" EXIT
printf "%s\n%s\n" "$srun_user" "$srun_pass" |
    systemd-creds encrypt --name=srun_cred - "$tmp"
chmod 0600 "$tmp"
mv -fT -- "$tmp" /etc/srun-login/srun.cred
'
```

运行一次服务确认配置，再启用开机定时检查。

```bash
sudo systemctl daemon-reload
sudo systemctl start srun-reconnect.service
sudo journalctl -u srun-reconnect.service -n 20 --no-pager
sudo systemctl enable --now srun-reconnect.timer
```

如果服务器此时在线，首次启动只会检查认证状态。真正的登录会在下次网关报告离线时发生。如果日志持续出现认证被拒，可以运行 `sudo systemctl disable --now srun-reconnect.timer` 暂停检查，更新凭据后再启用。手动退出前也可以暂停定时器。

如果服务器使用不同的网关或认证入口，可以在 `sudo systemctl edit srun-reconnect.service` 中用 `Environment=` 设置 `SRUN_URL` 或 `SRUN_AC_ID`。

本机 root 能访问运行时凭据。默认情况下，`systemd-creds` 使用本机密钥，并在可用时结合 TPM2。没有 TPM2 时，同时包含密文和系统解密密钥的整盘备份也能用于解密。迁移时请在新服务器上重新录入。旧版 systemd 如果支持 `LoadCredential=`，可以改用 root 所有、权限为 `0600` 的两行明文凭据文件，由 systemd 交给服务读取。系统没有 systemd 时，也可以用 cron 定期调用普通登录命令，并使用专用用户和 `0600` 的 `~/.srun_cred`。

### 多人共用服务器

同一台 Linux 服务器上的新版脚本共用一把锁。登录、`--reconnect` 和 `--logout` 任一命令正在运行时，重复启动的维护命令会提示并跳过。进程退出后自动释放锁。`--status` 随时可以运行。

管理员部署一份系统级定时器和加密凭据即可。已部署定时器时，更新 `/opt/srun-login/srun_login.py` 就能启用这把锁，手动执行时也用这一份脚本。锁只协调同一 Linux 网络命名空间内的新版脚本。旧版脚本、其他客户端或使用独立网络的容器需要单独协调。共用同一校园网出口 IP 时，手动注销仍可能使其他用户掉线。

## 证书与退出

脚本默认验证 HTTPS 证书。如果网关使用校方提供的 CA 证书，可用 `--ca-file /path/to/campus-ca.pem` 加载该证书。`--insecure` 会关闭证书校验，仅用于明确需要兼容原脚本行为的场景。

`python3 srun_login.py --logout` 会退出当前认证会话。如果当前 SSH 连接依赖该会话，退出可能中断连接。

运行 `python3 srun_login.py --help` 可查看全部参数。

# SRun 校园网登录脚本

在 Linux 服务器上通过命令行登录深澜（SRun）校园网，也可以查询认证状态或退出当前会话。适用于使用 `srun_bx1` 登录流程和 JSONP 接口的网关。

## 迁移到另一台服务器

只需复制 `srun_login.py`。脚本使用 Python 3 标准库，无需 `pip install`，也没有配套的 Python 模块。请确认目标服务器能直连校园网认证网关。脚本会直连网关，环境变量里的 HTTP 代理设置不会生效。

在目标服务器上设置网关地址和认证入口编号。若目标服务器在原来的认证网络内，可以先用旧脚本里的 `BASE` 和 `AC_ID`。校园网有多个认证入口时，使用目标服务器所在网段对应的值。

```bash
export SRUN_URL='https://portal.example.edu'
export SRUN_AC_ID='1'

python3 srun_login.py --status
python3 srun_login.py
```

示例地址和编号请替换为实际值。也可以用 `--url` 和 `--ac-id` 传入，它们优先于环境变量。查询状态只需要网关地址。

## 账号配置

默认交互式输入账号和密码。无人值守时可以注入 `SRUN_USER` 和 `SRUN_PASS`，或在目标服务器当前用户的家目录里配置 `~/.srun_cred`。文件第一行是账号，第二行是密码。两项环境变量均非空时优先使用它们，其次读取凭据文件。

```bash
chmod 600 ~/.srun_cred
```

在每台服务器上单独配置凭据，避免将凭据文件提交到 Git，也避免把明文密码直接写进交互式命令历史。

## 证书与退出

脚本默认验证 HTTPS 证书。如果网关使用校方提供的 CA 证书，可用 `--ca-file /path/to/campus-ca.pem` 加载该证书。`--insecure` 会关闭证书校验，仅用于明确需要兼容原脚本行为的场景。

`python3 srun_login.py --logout` 会退出当前认证会话。如果当前 SSH 连接依赖该会话，退出可能中断连接。

运行 `python3 srun_login.py --help` 可查看全部参数。

```md
# 安装与卸载说明

本项目当前推荐在 **Ubuntu / Debian** 系统上安装。  
为避免脚本执行权限问题，**请统一使用 `bash` 执行脚本**，不要直接用 `./install.sh`。

---

## 一、安装前准备

进入项目目录：

```bash
cd /root/lvyousms-web
```

推荐使用 root 用户，或具备 sudo 权限的用户执行。

---

## 二、安装

执行安装命令：

```bash
sudo bash install.sh install
```

安装过程中会提示你输入或确认以下内容：

- 服务端口
- 是否保留旧的 UI 密码
- 其他必要配置项

按提示操作即可。

---

## 三、安装后检查

安装完成后，建议检查服务状态：

```bash
sudo systemctl status board-manager-v4 --no-pager
sudo systemctl status board-manager-v6 --no-pager
```

查看配置文件：

```bash
cat /etc/board-manager.conf
```

---

## 四、访问方式

浏览器访问：

```text
http://服务器IP:端口/
```

例如：

```text
http://192.168.1.4:8000/
```

---

## 五、如果出现 `{"detail":"UI not built"}`

这表示前端静态文件没有成功部署到运行目录。  
可手动重新构建并部署前端：

```bash
cd /root/lvyousms-web/frontend
npm run build
sudo mkdir -p /opt/board-manager/static
sudo cp -a /root/lvyousms-web/frontend/dist/. /opt/board-manager/static/
sudo systemctl restart board-manager-v4 board-manager-v6
```

然后重新访问首页。

---

## 六、卸载

如需卸载旧版本或当前版本，执行：

```bash
cd /root/lvyousms-web
sudo bash uninstall.sh
```

---

## 七、手动彻底卸载（如卸载脚本不完整）

```bash
sudo systemctl stop board-manager-v4 board-manager-v6
sudo systemctl disable board-manager-v4 board-manager-v6
sudo rm -f /etc/systemd/system/board-manager-v4.service
sudo rm -f /etc/systemd/system/board-manager-v6.service
sudo systemctl daemon-reload

sudo rm -rf /opt/board-manager
sudo rm -f /etc/board-manager.conf
```

如果还要删除源码目录：

```bash
rm -rf /root/lvyousms-web
```

---

## 八、重新安装

卸载完成后，重新进入项目目录执行：

```bash
cd /root/lvyousms-web
sudo bash install.sh install
```

---

## 九、说明

当前版本建议使用以下方式执行脚本：

### 安装
```bash
sudo bash install.sh install
```

### 卸载
```bash
sudo bash uninstall.sh
```

这样可以避免由于脚本没有执行权限导致的安装失败问题。

---

## 十、建议备份

如果准备重装或迁移，建议先备份：

```bash
tar -czf /root/board-manager-backup-$(date +%F-%H%M).tar.gz /opt/board-manager /etc/board-manager.conf
```

---

## 十一、当前版本已知情况

- 当前版本可正常安装和运行
- 前端若未正确部署，可能出现 `UI not built`
- 如遇该问题，可按上文“第五节”手动重新部署前端
- 扫描后列表自动刷新体验仍有待进一步优化，但不影响基础使用
```